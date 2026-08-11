"""M6 verification — tracing, the eval report, and the admin guards."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from evals.run import ItemResult, build_report, load_golden_set
from server.observability import DEFAULT_TAGS, Tracer, tracer


@pytest.fixture(scope="module")
def client():
    from server.main import app

    with TestClient(app) as c:
        yield c


# --- golden set ----------------------------------------------------------------------


def test_golden_set_has_twenty_items_of_the_required_kinds():
    items = load_golden_set()
    assert len(items) == 20
    kinds = {}
    for item in items:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    # spec 06: at least 8 knowledge, 6 data, 3 redirect, 3 permission
    assert kinds["knowledge"] >= 8
    assert kinds["data"] >= 6
    assert kinds["redirect"] >= 3
    assert kinds["forbidden"] >= 3


def test_golden_set_entries_have_the_required_fields():
    for item in load_golden_set():
        assert {"id", "user", "question", "expected_answer", "kind"} <= set(item)
        assert item["user"] in ("alice", "bob", "asha", "cora")
        assert item["kind"] in ("knowledge", "data", "redirect", "forbidden")


def test_the_412_march_line_is_in_the_golden_set():
    values = [v for i in load_golden_set() for v in i.get("expected_values", [])]
    assert "412.00" in values


# --- report shape ---------------------------------------------------------------------


def _result(**kw) -> ItemResult:
    base = {
        "id": "x", "kind": "knowledge", "user": "alice", "question": "q",
        "response_type": "answer", "answer": "a", "passed": True, "metrics": {},
        "seconds": 1.0,
    }
    base.update(kw)
    return ItemResult(**base)


def test_report_contains_per_item_rows_and_averages():
    results = [
        _result(id="k01", metrics={"faithfulness": 1.0, "answer_correctness": 0.9,
                                   "context_precision": 1.0}),
        _result(id="d01", kind="data", response_type="rows_answer", metrics={"exact_match": 1.0}),
        _result(id="r01", kind="redirect", response_type="redirect", metrics={"refused": 1.0}),
    ]
    report, summary = build_report(results, datetime.now(UTC))

    for metric in ("faithfulness", "answer_correctness", "context_precision"):
        assert metric in report
        assert metric in summary
    for item_id in ("k01", "d01", "r01"):
        assert item_id in report
    assert summary["data_exact_match"] == 1.0
    assert summary["redirect_forbidden"] == 1.0
    assert summary["items"] == 3


def test_report_gate_is_data_and_refusals_only():
    """The LLM-judged metrics are report-only under the dev model (spec 06)."""
    results = [
        _result(id="k01", metrics={"faithfulness": 0.1, "answer_correctness": 0.1,
                                   "context_precision": 0.1}),
        _result(id="d01", kind="data", metrics={"exact_match": 1.0}),
        _result(id="r01", kind="redirect", metrics={"refused": 1.0}),
    ]
    _, summary = build_report(results, datetime.now(UTC))
    assert summary["faithfulness"] == 0.1          # reported
    assert summary["data_exact_match"] == 1.0      # enforced
    assert summary["redirect_forbidden"] == 1.0    # enforced


# --- tracer ----------------------------------------------------------------------------


def test_console_tracer_writes_json_lines(tmp_path, monkeypatch):
    local = Tracer()
    monkeypatch.setattr("server.observability.TRACE_FILE", tmp_path / "traces.jsonl")
    with local.trace("chat.turn", user_id="u-alice") as root:
        root.set(route="knowledge")
        with local.span("node.route") as child:
            child.set(route="knowledge")

    lines = (tmp_path / "traces.jsonl").read_text().splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert {r["trace_id"] for r in records} == {records[0]["trace_id"]}, "one trace per turn"
    child_record = next(r for r in records if r["name"] == "node.route")
    assert child_record["parent_id"] is not None


def test_spans_carry_the_tags_spec_06_requires(tmp_path, monkeypatch):
    local = Tracer()
    monkeypatch.setattr("server.observability.TRACE_FILE", tmp_path / "t.jsonl")
    with local.trace("chat.turn") as span:
        span.set(route="data", gate_result="ok", sql_valid=True, action_kind=None)
    record = json.loads((tmp_path / "t.jsonl").read_text().splitlines()[0])
    for tag in DEFAULT_TAGS:
        assert tag in record
    assert record["escalated"] is False


def test_tracing_failure_never_breaks_the_caller(monkeypatch):
    local = Tracer()
    monkeypatch.setattr("server.observability.TRACE_FILE", None)
    with local.trace("chat.turn") as span:  # TRACE_FILE=None makes the write blow up
        span.set(route="knowledge")
    # Reaching here at all is the assertion.


def test_span_records_an_exception_and_reraises(tmp_path, monkeypatch):
    local = Tracer()
    monkeypatch.setattr("server.observability.TRACE_FILE", tmp_path / "t.jsonl")
    with pytest.raises(ValueError), local.span("tool.boom"):
        raise ValueError("boom")
    record = json.loads((tmp_path / "t.jsonl").read_text().splitlines()[0])
    assert "ValueError" in record["error"]


def test_default_sink_is_the_console_file():
    assert tracer.sink == "console"


# --- admin guards -----------------------------------------------------------------------


def test_admin_endpoints_require_admin(client, tokens):
    for path in ("/admin/summary", "/admin/audit", "/admin/evals", "/admin/traces"):
        assert client.get(path).status_code == 401, path
        assert client.get(
            path, headers={"Authorization": f"Bearer {tokens['alice']}"}
        ).status_code == 404, path
        assert client.get(
            path, headers={"Authorization": f"Bearer {tokens['cora']}"}
        ).status_code == 200, path


def test_admin_summary_reports_the_latest_eval(client, tokens):
    body = client.get(
        "/admin/summary", headers={"Authorization": f"Bearer {tokens['cora']}"}
    ).json()
    assert body["trace_sink"] in ("console", "langfuse")
    if body["latest_eval"] is None:
        # A freshly seeded database has no eval run to report, which is the normal state
        # in CI. The endpoint answering at all is asserted above; what it reports once
        # there IS a run belongs to a machine that can run one.
        pytest.skip("no eval recorded yet — run `make eval`")
    assert body["latest_eval"]["items"] == 20


def test_admin_traces_are_readable(client, tokens):
    body = client.get(
        "/admin/traces", headers={"Authorization": f"Bearer {tokens['cora']}"}
    ).json()
    assert body["sink"] == "console"
    assert isinstance(body["spans"], list)


# --- the correctness metric must not confuse "more complete" with "wrong" -------------

CORRECTNESS_Q = "How long is data kept on the instrument PCs?"
CORRECTNESS_REF = "Data on an instrument PC is deleted 30 days after acquisition."
CORRECTNESS_CTX = [
    "Data Management and Retention (v1.3)\n"
    "Data written to an instrument PC is deleted 30 days after acquisition, "
    "automatically and without warning. Instrument PCs are working space, not an "
    "archive.\n\nEach core exposes a transfer share for moving data off the instrument. "
    "Data on the transfer share is retained for 90 days, then deleted. The share is not "
    "backed up."
]


def _correctness(answer: str) -> float:
    from evals.metrics import answer_correctness

    return answer_correctness(answer, CORRECTNESS_REF, CORRECTNESS_Q, CORRECTNESS_CTX)


@pytest.mark.llm
def test_extra_detail_the_sources_support_is_not_scored_as_an_error():
    """Regression (k04): the answer quoted the retention document verbatim, added the
    transfer-share figure from the same document, and scored 0.72 — the second sentence
    was charged as a false positive purely because the one-line reference omitted it.
    RAGAS assumes a complete reference; ours are deliberately terse, so the metric was
    measuring verbosity rather than correctness.
    """
    terse = _correctness("Data on the instrument PCs is deleted 30 days after acquisition [1].")
    fuller = _correctness(
        "Data on the instrument PCs is deleted 30 days after acquisition [1]. "
        "Data on the transfer share is retained for 90 days [1]."
    )
    assert terse >= 0.80, f"an answer matching the reference should score well, got {terse}"
    assert fuller >= 0.80, f"correct sourced detail must not be punished, got {fuller}"


@pytest.mark.llm
def test_a_fact_in_neither_the_reference_nor_the_sources_is_still_punished():
    """Showing the judge the sources must not make it blind to invention."""
    invented = _correctness(
        "Data on the instrument PCs is deleted 30 days after acquisition [1]. "
        "Backups are kept on tape for seven years [1]."
    )
    assert invented < 0.80, f"an invented fact must cost the score, got {invented}"


@pytest.mark.llm
def test_contradicting_the_reference_is_still_punished():
    wrong = _correctness("Data on the instrument PCs is deleted 60 days after acquisition [1].")
    assert wrong < 0.80, f"a contradicted fact must cost the score, got {wrong}"


@pytest.mark.llm
def test_omitting_what_the_reference_states_is_still_punished():
    """The failure mode of the fix: judging FN against the sources instead of the
    reference let an answer that never addressed the question score 0.90, because
    everything it said happened to be sourced."""
    off_topic = _correctness("Instrument PCs are working space, not an archive [1].")
    assert off_topic < 0.80, f"missing the reference fact must cost the score, got {off_topic}"


# --- splitting a list of alternatives must keep the direction of the relation ---------

BILLING_CTX = [
    "Billing FAQ > How am I charged? (v2.0)\n"
    "Invoices are issued monthly, in arrears, in the first week of the following month. "
    "Each invoice covers one account code for one calendar month, and its total is the "
    "sum of its lines. A line corresponds to a chargeable item: instrument time, a "
    "service request, or a consumable."
]
BILLING_Q = "When are invoices issued?"


@pytest.mark.llm
def test_enumerations_are_split_with_the_member_as_the_subject():
    """Regression (k07): "a line ... : instrument time, a service request, or a
    consumable" was split into "A chargeable item is a consumable" — which claims every
    chargeable item is one, and is false. The judge rejected it, correctly, and a
    sentence quoting its source verbatim lost a tenth of its faithfulness. The bug was in
    the decomposition, not the judging.
    """
    from evals.metrics import _statements

    statements = _statements(
        "A line corresponds to a chargeable item: instrument time, a service request, "
        "or a consumable [1]."
    )
    inverted = [s for s in statements if s.lower().startswith("a chargeable item is")]
    assert not inverted, f"relation inverted into a false universal: {inverted}"
    assert any("consumable" in s.lower() for s in statements), "the member must survive"


@pytest.mark.llm
def test_a_sentence_quoting_its_source_scores_full_faithfulness():
    from evals.metrics import faithfulness

    score = faithfulness(
        "Invoices are issued monthly, in arrears, in the first week of the following "
        "month. A line corresponds to a chargeable item: instrument time, a service "
        "request, or a consumable [1].",
        BILLING_CTX, BILLING_Q,
    )
    assert score >= 0.95, f"a verbatim-sourced answer should score ~1.0, got {score}"


@pytest.mark.llm
@pytest.mark.parametrize(
    "answer",
    [
        "Invoices are issued every fortnight, in the first week of the following month [1].",
        "Invoices are issued monthly, in arrears [1]. A 12% late-payment surcharge "
        "applies after 30 days [1].",
        "Invoices are emailed by the finance office every Friday afternoon [1].",
    ],
)
def test_unfaithful_answers_are_still_caught(answer):
    """The splitter fix must not make the metric blind to invention."""
    from evals.metrics import faithfulness

    assert faithfulness(answer, BILLING_CTX, BILLING_Q) < 0.90


@pytest.mark.llm
def test_a_false_universal_the_answer_actually_asserts_is_still_rejected():
    """The fix stops the splitter manufacturing that claim — not the metric accepting it."""
    from evals.metrics import faithfulness

    score = faithfulness(
        "Every chargeable item on an invoice is a consumable [1].", BILLING_CTX, BILLING_Q
    )
    assert score < 0.50, f"the answer itself asserts something false, got {score}"


# --- context relevance must be evidence-backed ----------------------------------------


def test_quote_matching_tolerates_punctuation_and_reflow():
    """The judge rewrites dashes and reflows line breaks. Rejecting a correct sentence on
    an em dash made k06 score 0.0 with the right document sitting at rank 1."""
    from evals.metrics import _quotes_the_context

    assert _quotes_the_context(
        "Spinning Disk SD1 — the right choice for live-cell imaging",
        "Spinning Disk SD1 - the right choice for live-cell imaging and more",
    )
    assert _quotes_the_context(
        "the right choice for live-cell\nimaging", "the right choice for live-cell imaging"
    )


def test_quote_matching_rejects_evidence_that_is_not_there():
    from evals.metrics import _quotes_the_context

    assert not _quotes_the_context(
        "invoices are issued every fortnight", "invoices are issued monthly in arrears"
    )
    assert not _quotes_the_context(
        "spinning disk imaging faster second",
        "Spinning Disk SD1 is good. Imaging faster than one frame per second.",
    ), "the span has to be contiguous, not words gathered from across the document"


def test_quote_matching_rejects_a_fragment_too_short_to_prove_anything():
    from evals.metrics import _quotes_the_context

    assert not _quotes_the_context("live-cell imaging", "the right choice for live-cell imaging")


def _corpus_chunk(breadcrumb_like: str) -> str:
    """The real chunk, as the metric sees it in a run.

    Deliberately not a hand-written excerpt: the judgement depends on the whole chunk,
    and a two-sentence stand-in is a different question from the one the eval asks.
    """
    from sqlalchemy import text as sql_text

    from server.db import session_scope

    with session_scope() as s:
        row = s.execute(
            sql_text(
                "SELECT breadcrumb || E'\\n' || text FROM echomind.chunks "
                "WHERE breadcrumb LIKE :p LIMIT 1"
            ),
            {"p": breadcrumb_like},
        ).scalar()
    if not row:
        pytest.skip("corpus not ingested — run `python -m server.rag.ingest db/corpus`")
    return row


@pytest.mark.llm
@pytest.mark.parametrize(
    "breadcrumb,reference,question",
    [
        (
            "Confocal C2 Standard%",
            "Biosafety Level 2 certification is valid for 24 months.",
            "How long is Biosafety Level 2 certification valid for?",
        ),
        (
            "Training Module 2%",
            "Data on an instrument PC is deleted 30 days after acquisition.",
            "How long is data kept on the instrument PCs?",
        ),
    ],
)
def test_a_context_on_the_topic_without_the_fact_is_not_counted_useful(
    breadcrumb, reference, question
):
    """Regression: the Confocal SOP requires "current Biosafety Level 2 certification"
    but never says how long one lasts, and Training Module 2 says data has a lifetime
    without giving it. Both were credited, which dragged k03 and k04 to 0.833."""
    from evals.metrics import context_precision

    score = context_precision([_corpus_chunk(breadcrumb)], reference, question)
    assert score == 0.0, f"on-topic is not fact-bearing ({breadcrumb}), got {score}"


@pytest.mark.llm
def test_a_context_carrying_the_fact_is_still_counted_useful():
    """The tightening must not stop crediting the document that answers the question."""
    from evals.metrics import context_precision

    bearing = (
        "Training Requirements and Certification > General rule (v1.4)\n"
        "Biosafety Level 2 certification is valid for 24 months from the date of award."
    )
    assert context_precision(
        [bearing],
        "Biosafety Level 2 certification is valid for 24 months.",
        "How long is Biosafety Level 2 certification valid for?",
    ) == 1.0


# --- claimed omissions and inventions are checked, not taken on trust ------------------


def test_a_missing_fact_whose_number_is_in_the_answer_is_not_missing():
    """Regression: an answer quoting its reference verbatim was charged a phantom FN.
    Three rewordings and a source-free judging call each fixed some cases and broke
    others — the prompt is the wrong tool. The numbers settle it: "retained for 30 days"
    and "deleted 30 days after acquisition" are one fact from opposite ends.
    """
    from evals.metrics import _omission_is_real

    assert not _omission_is_real(
        "deleted 30 days after acquisition", "Data is retained for 30 days after acquisition."
    )
    assert not _omission_is_real("valid for 24 months", "Certification is valid for 24 months.")


def test_a_genuinely_absent_fact_is_still_counted():
    from evals.metrics import _omission_is_real

    assert _omission_is_real(
        "deleted 30 days after acquisition", "The transfer share is retained for 90 days."
    )
    assert _omission_is_real("valid for 24 months", "Certification must be kept current.")


def test_citation_markers_do_not_count_as_numbers():
    """Regression: "[1]" put a stray 1 into the claim's numbers, which no source
    contained, so every cited sentence looked unsupported."""
    from evals.metrics import _omission_is_real

    assert not _omission_is_real(
        "the transfer share is retained for 90 days [1].",
        "Data on the transfer share is retained for 90 days, then deleted.",
    )


def test_claims_without_numbers_fall_back_to_substance():
    from evals.metrics import _omission_is_real

    claim = "the Spinning Disk SD1 is right for live-cell imaging"
    assert not _omission_is_real(claim, "For live-cell imaging use the Spinning Disk SD1.")
    assert _omission_is_real(claim, "Bookings require an account code.")


@pytest.mark.llm
def test_correctness_separates_fuller_from_wrong():
    """The whole point: more detail must score well, invention and omission must not."""
    fuller = _correctness(
        "Data on the instrument PCs is deleted 30 days after acquisition [1]. "
        "Data on the transfer share is retained for 90 days [1]."
    )
    invented = _correctness(
        "Data on the instrument PCs is deleted 30 days after acquisition [1]. "
        "A 12% surcharge applies after 45 days [1]."
    )
    omits = _correctness("Data on the transfer share is retained for 90 days [1].")
    assert fuller >= 0.90, f"correct sourced detail must score well, got {fuller}"
    assert invented < 0.85, f"an invented figure must cost the score, got {invented}"
    assert omits < 0.85, f"omitting the reference fact must cost the score, got {omits}"
