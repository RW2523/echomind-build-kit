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
    base = dict(
        id="x", kind="knowledge", user="alice", question="q", response_type="answer",
        answer="a", passed=True, metrics={}, seconds=1.0,
    )
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
    with pytest.raises(ValueError):
        with local.span("tool.boom"):
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
    assert body["latest_eval"] is not None, "run `make eval` first"
    assert body["latest_eval"]["items"] == 20
    assert body["trace_sink"] in ("console", "langfuse")


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
