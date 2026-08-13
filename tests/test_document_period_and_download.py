"""A dated document is only true of its date, and only useful if it can be fetched.

Three things this file pins down, each of which shipped broken:

* the period on an invoice comes from the user, never from the planner;
* however they said the month, the tools get YYYY-MM;
* the file behind an executed action is reachable by its owner and by nobody else.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from server.agent import action as action_mod
from server.agent.graph import _answering_our_question

# --- the period must be the user's, and in the form the tools take -------------------


@pytest.mark.parametrize(
    "said, expected",
    [
        ("2026-03", "2026-03"),
        ("March 2026", "2026-03"),
        ("march", "2026-03"),
        ("Mar", "2026-03"),
        ("September", "2025-09"),  # not yet reached in 2026 — they mean the last one
        ("December 2025", "2025-12"),
        ("this month", "2026-08"),
        ("last month", "2026-07"),
        ("the previous month", "2026-07"),
        ("invoice for March 2026 please", "2026-03"),
        ("", None),
        (None, None),
        ("whenever", None),
        ("the usual one", None),
    ],
)
def test_however_they_said_the_month_the_tools_get_yyyy_mm(said, expected):
    assert action_mod._normalise_period(said, date(2026, 8, 12)) == expected


def test_a_bare_month_means_the_most_recent_one_that_has_happened():
    """Asked in August for "the March invoice", nobody means next March."""
    assert action_mod._normalise_period("March", date(2026, 8, 12)) == "2026-03"
    assert action_mod._normalise_period("March", date(2026, 2, 1)) == "2025-03"


def test_bare_invoice_request_asks_for_the_period():
    plan = {
        "tool": "generate_document",
        "arguments": {"template": "invoice_statement", "params": {"account_code": "ACC-A1"}},
    }
    guarded = action_mod.require_document_period(plan, "give me an invoice", "")
    assert guarded["tool"] is None
    assert "period" in guarded["ask"].lower()
    assert guarded["missing"] == "period"


def test_a_period_the_planner_invented_is_not_accepted():
    """The failure this guard exists for: a confident, finished PDF for a month nobody
    named. The planner supplied it, the conversation never did, so it has to ask."""
    plan = {
        "tool": "generate_document",
        "arguments": {
            "template": "invoice_statement",
            "params": {"account_code": "ACC-A1", "period": "2026-08"},
        },
    }
    guarded = action_mod.require_document_period(plan, "send me my invoice", "")
    assert guarded["tool"] is None, "a period only the model knows is not grounded"


def test_a_period_the_user_named_earlier_is_accepted():
    plan = {
        "tool": "generate_document",
        "arguments": {"template": "invoice_statement", "params": {"account_code": "ACC-A1"}},
    }
    history = "  user: what did I spend in March 2026?\n  assistant (rows_answer): 2,431.00"
    guarded = action_mod.require_document_period(plan, "put that in a pdf", history)
    assert guarded["tool"] == "generate_document"
    assert guarded["arguments"]["params"]["period"] == "2026-03"


def test_the_question_comes_even_when_the_planner_produced_no_call():
    """A dead end used to look like a refusal. "give me an invoice" with nothing behind it
    left the planner with no tool and no question, and the user got the generic redirect."""
    guarded = action_mod.require_document_period({"tool": None}, "give me an invoice", "")
    assert guarded["tool"] is None
    assert "period" in guarded["ask"].lower()


def test_documents_without_a_period_are_left_alone():
    plan = {
        "tool": "generate_document",
        "arguments": {"template": "facility_directory", "params": {}},
    }
    assert action_mod.require_document_period(plan, "send me the directory", "") == plan


def test_other_tools_are_left_alone():
    plan = {"tool": "create_booking", "arguments": {"instrument_id": "i-1"}}
    assert action_mod.require_document_period(plan, "book it", "") == plan


# --- the reply to a question belongs to whoever asked it -----------------------------


def test_an_answer_to_a_clarification_returns_to_the_branch_that_asked():
    """"March 2026" on its own reads as a billing lookup. In context it is the rest of a
    document request, and the router cannot see that from two words."""
    state = {
        "message": "March 2026",
        "history": [{"q": "give me an invoice", "a": "Which period?", "type": "clarify",
                     "route": "action"}],
    }
    assert _answering_our_question(state) == "action"


def test_only_the_immediately_preceding_clarification_counts():
    state = {
        "message": "March 2026",
        "history": [
            {"q": "give me an invoice", "a": "Which period?", "type": "clarify",
             "route": "action"},
            {"q": "what is the confocal policy?", "a": "…", "type": "answer",
             "route": "knowledge"},
        ],
    }
    assert _answering_our_question(state) is None


def test_no_history_means_no_override():
    assert _answering_our_question({"message": "hi", "history": []}) is None


def test_an_unknown_branch_is_not_honoured():
    state = {
        "message": "March 2026",
        "history": [{"q": "x", "a": "y", "type": "clarify", "route": "nonsense"}],
    }
    assert _answering_our_question(state) is None


# --- the records a document was built from -------------------------------------------


def test_money_keeps_every_digit_it_was_given():
    """Rounding a charge to a float would be a quiet lie in a document about money."""
    from decimal import Decimal

    from server.mcp.actions import _plain

    assert _plain(Decimal("999.05")) == "999.05"
    assert _plain({"amount": Decimal("1.10")}) == {"amount": "1.10"}


def test_timestamps_and_ids_survive_the_trip_into_the_audit_row():
    import json
    from datetime import datetime
    from uuid import UUID

    from server.mcp.actions import _plain

    plain = _plain(
        {"starts_at": datetime(2026, 3, 4, 9, 0), "id": UUID(int=7), "rows": [{"n": 1}]}
    )
    json.dumps(plain)  # the actual requirement: it has to serialise
    assert plain["starts_at"] == "2026-03-04T09:00:00"


def test_a_document_carries_the_rows_it_was_built_from(ctxs):
    """A statement of charges is only as trustworthy as the ledger behind it, so the
    ledger travels with it rather than being something the reader has to take on faith."""
    from server.mcp import actions as actions_mod
    from server.mcp import tools as tools_mod

    ctx = ctxs["asha"]
    pending = tools_mod.call(
        ctx,
        "generate_document",
        {
            "template": "invoice_statement",
            "params": {"account_code": "ACC-A1", "period": "2026-03"},
            "format": "pdf",
        },
    )
    result = actions_mod.approve(ctx, pending["action_id"])["result"]

    assert result["record_count"] > 0
    assert len(result["records"]) == result["record_count"]
    assert result["records_truncated"] is False
    assert all(isinstance(r, dict) for r in result["records"])


# --- the file itself -----------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from server.main import app

    with TestClient(app) as c:
        yield c


def _executed_document(client, token) -> str:
    from server.mcp import tools as tools_mod
    from server.auth import decode

    ctx = decode(token)
    pending = tools_mod.call(
        ctx,
        "generate_document",
        {
            "template": "invoice_statement",
            "params": {"account_code": "ACC-A1", "period": "2026-03"},
            "format": "pdf",
        },
    )
    action_id = pending["action_id"]
    client.post(f"/actions/{action_id}/approve", headers={"Authorization": f"Bearer {token}"})
    return action_id


def test_the_owner_gets_a_real_pdf(client, tokens):
    action_id = _executed_document(client, tokens["asha"])
    r = client.get(
        f"/actions/{action_id}/document",
        headers={"Authorization": f"Bearer {tokens['asha']}"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-", "a file that is not a PDF is not a downloadable PDF"
    assert "attachment" in r.headers["content-disposition"]


def test_nobody_elses_document_is_reachable(client, tokens):
    action_id = _executed_document(client, tokens["asha"])
    r = client.get(
        f"/actions/{action_id}/document",
        headers={"Authorization": f"Bearer {tokens['bob']}"},
    )
    assert r.status_code == 404, "not found is the same answer whether it exists or not"


def test_an_unauthenticated_request_gets_nothing(client):
    r = client.get("/actions/act-does-not-exist/document")
    assert r.status_code in (401, 403)


# --- the invoice, laid out as an invoice ---------------------------------------------
#
# A prettier document that is loose with its numbers would be worse than the plain one,
# so these check the layout carries the same guarantees the Markdown builder does.


def _pdf_text(data: bytes) -> str:
    import io

    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


_LINES = [
    {"instrument": "Confocal C2", "description": "Imaging — week 2", "amount": "252.00"},
    {"instrument": "Cryo-EM Titan", "description": "Session", "amount": "290.00"},
]


def test_the_invoice_carries_the_company_it_came_from():
    from server.mcp import documents

    text = _pdf_text(documents.invoice_pdf("ACC-A1", "2026-03", _LINES, total="542.00"))
    assert "Infinity X" in text
    assert "INVOICE" in text
    assert "ACC-A1" in text and "2026-03" in text


def test_every_line_and_the_supplied_total_appear():
    from server.mcp import documents

    text = _pdf_text(documents.invoice_pdf("ACC-A1", "2026-03", _LINES, total="542.00"))
    assert "Confocal C2" in text and "252.00" in text
    assert "Cryo-EM Titan" in text and "290.00" in text
    assert "542.00" in text


def test_a_derived_total_says_it_was_derived():
    """The reader has to be able to tell a figure the ledger gave us from one we added up."""
    from server.mcp import documents

    text = _pdf_text(documents.invoice_pdf("ACC-A1", "2026-03", _LINES, total=None))
    assert "542.00" in text
    assert "derived" in text.lower()


def test_a_total_that_disagrees_with_its_own_lines_is_printed_not_reconciled():
    """The one thing an invoice may never do is quietly print a total its lines do not
    add up to. Both figures appear, and the disagreement is stated."""
    from server.mcp import documents

    text = _pdf_text(documents.invoice_pdf("ACC-A1", "2026-03", _LINES, total="999.00"))
    assert "999.00" in text
    assert "542.00" in text
    assert "does not match" in text.lower()


def test_nothing_supplied_is_not_reported_as_nothing_owed():
    """$0.00 tells the reader they owe nothing, which is a different claim from
    "no total was supplied"."""
    from server.mcp import documents

    text = _pdf_text(documents.invoice_pdf("ACC-A1", "2026-03", [], total=None))
    assert "0.00" not in text
    assert "not recorded" in text.lower()
    assert "No charges in this period" in text


def test_the_hours_column_appears_only_when_the_lines_have_hours():
    """A column of em-dashes reads as data that went missing."""
    from server.mcp import documents

    without = _pdf_text(documents.invoice_pdf("ACC-A1", "2026-03", _LINES, total="542.00"))
    assert "Hours" not in without

    with_hours = _pdf_text(
        documents.invoice_pdf(
            "ACC-A1", "2026-03",
            [{**_LINES[0], "hours": 4}, {**_LINES[1], "hours": 2}], total="542.00",
        )
    )
    assert "Hours" in with_hours


def test_the_review_window_is_on_the_page():
    """It is the last thing they read before the clock runs out."""
    from server.mcp import documents

    text = _pdf_text(documents.invoice_pdf("ACC-A1", "2026-03", _LINES, total="542.00"))
    assert str(documents.INVOICE_REVIEW_DAYS) in text
    assert "review period" in text.lower()


def test_a_long_statement_still_renders():
    """Enough lines to force a second page — the header row repeats and nothing throws."""
    from server.mcp import documents

    many = [
        {"instrument": f"Instrument {i}", "description": f"Session {i}", "amount": "10.00"}
        for i in range(60)
    ]
    data = documents.invoice_pdf("ACC-A1", "2026-03", many, total="600.00")
    assert data[:5] == b"%PDF-"
    assert "600.00" in _pdf_text(data)


def test_a_pipe_in_a_description_does_not_break_the_layout():
    from server.mcp import documents

    data = documents.invoice_pdf(
        "ACC-A1", "2026-03",
        [{"instrument": "C2", "description": "a | b <b>c</b>", "amount": "1.00"}],
        total="1.00",
    )
    assert data[:5] == b"%PDF-"


def test_the_generated_invoice_pdf_is_the_laid_out_one_not_the_markdown_one(ctxs):
    """The wiring, not the layout: an invoice asked for as a PDF has to come out as the
    form, and the same request as Markdown has to stay Markdown."""
    from pathlib import Path

    from server.config import REPO_ROOT
    from server.mcp import actions as actions_mod
    from server.mcp import tools as tools_mod

    ctx = ctxs["asha"]
    params = {"account_code": "ACC-A1", "period": "2026-03"}
    pending = tools_mod.call(
        ctx, "generate_document",
        {"template": "invoice_statement", "params": params, "format": "pdf"},
    )
    result = actions_mod.approve(ctx, pending["action_id"])["result"]
    text = _pdf_text((REPO_ROOT / result["path"]).read_bytes())
    assert "Infinity X" in text and "INVOICE" in text

    pending = tools_mod.call(
        ctx, "generate_document",
        {"template": "invoice_statement", "params": params, "format": "md"},
    )
    result = actions_mod.approve(ctx, pending["action_id"])["result"]
    markdown = (REPO_ROOT / result["path"]).read_text()
    assert markdown.startswith("# Invoice statement")


def test_changing_the_subject_is_not_dragged_back_to_the_waiting_branch():
    """Someone asked which period who instead asks about policy has moved on."""
    history = [{"q": "give me an invoice", "a": "Which period?", "type": "clarify",
                "route": "action"}]
    for moved_on in (
        "actually what is the confocal warm-up policy?",
        "never mind, how many bookings do I have this week?",
    ):
        assert _answering_our_question({"message": moved_on, "history": history}) is None


def test_a_wordier_answer_still_counts():
    history = [{"q": "give me an invoice", "a": "Which period?", "type": "clarify",
                "route": "action"}]
    assert _answering_our_question(
        {"message": "the March one please", "history": history}
    ) == "action"


# --- what an adversarial review found in the first version of this guard --------------
#
# Every case below rendered, or would have rendered, a real invoice for a month nobody
# named. They are grouped by the root cause rather than by symptom, because each root
# cause produced several.


@pytest.mark.parametrize(
    "said",
    [
        "Can you make me an invoice for ACC-A1? Maybe as a PDF.",  # maybe -> May
        "send Mark the invoice",                                   # Mark  -> March
        "I need to decide about my invoice",                       # decide -> December
        "novel imaging invoice",                                   # novel -> November
        "separate invoice please",                                 # separate -> September
        "octopus samples invoice",                                 # octopus -> October
        "invoice for the marketing team",                          # marketing -> March
        "may I have my invoice",                                   # the modal, not the month
        "why was my booking declined",                             # declined -> December
        "august company policy applies",                           # adjective, not a date
        "just the invoice please",
    ],
)
def test_an_ordinary_english_word_is_not_a_month(said):
    """`(jan|feb|mar|…)[a-z]*` matched every word merely beginning with a month prefix, so
    the guard written to stop invented periods was the thing inventing them."""
    assert action_mod._normalise_period(said, date(2026, 8, 12)) is None


@pytest.mark.parametrize(
    "said, expected",
    [
        ("invoice for March 2026", "2026-03"),
        ("the March invoice", "2026-03"),
        ("March", "2026-03"),
        ("march please", "2026-03"),
        ("for september", "2025-09"),
        ("Sept 2025", "2025-09"),
        ("2025, December", "2025-12"),
        ("give me the July invoice as a pdf", "2026-07"),
        ("the period of May", "2026-05"),
        ("covering April", "2026-04"),
    ],
)
def test_a_month_used_as_a_date_still_reads_as_one(said, expected):
    assert action_mod._normalise_period(said, date(2026, 8, 12)) == expected


def test_the_guards_own_question_is_not_evidence():
    """It used to read "For example March 2026, or 2026-03", which went into history
    verbatim — so whatever the user replied, the guard found its own suggestion and
    rendered March. A guard whose own question satisfies it is not a guard."""
    history = (
        "EARLIER IN THIS CONVERSATION (most recent last):\n"
        "  user: can you send me my invoice for ACC-A1\n"
        f"  assistant (clarify): {action_mod._PERIOD_REQUIRED['invoice_statement'][1]}"
    )
    for reply in ("yes please", "the latest one", "whatever you have", "the most recent one"):
        assert action_mod._grounded_period(reply, history, date(2026, 8, 12)) is None


def test_the_question_itself_contains_no_parseable_date():
    """Belt and braces: even if the filter above regressed, the text cannot ground it."""
    for _, question in action_mod._PERIOD_REQUIRED.values():
        assert action_mod._normalise_period(question, date(2026, 8, 12)) is None


def test_a_year_from_an_unrelated_turn_is_not_the_invoice_year():
    """Scanning the whole transcript for any 20xx turned "the Cryo-EM was installed in
    2019" into an invoice for March 2019."""
    history = (
        "EARLIER IN THIS CONVERSATION (most recent last):\n"
        "  user: when was the Cryo-EM installed?\n"
        "  assistant (rows_answer): The Cryo-EM Titan was installed in 2019, serviced 2024."
    )
    assert action_mod._grounded_period(
        "send me the March invoice for ACC-A1", history, date(2026, 8, 12)
    ) == "2026-03"


_TWO_INVOICES = (
    "EARLIER IN THIS CONVERSATION (most recent last):\n"
    "  user: what did I spend in 2026-03?\n"
    "  assistant (rows_answer): 2,431.00 for period 2026-03\n"
    "  user: and ACC-B2 in May 2026?\n"
    "  assistant (rows_answer): 980.00 for period 2026-05"
)


def test_the_month_just_named_beats_an_older_one_in_the_transcript():
    """The first match in the blob won, so "now give me the July invoice" rendered March
    because March appeared further up."""
    assert action_mod._grounded_period(
        "now give me the July invoice as a pdf", _TWO_INVOICES, date(2026, 8, 12)
    ) == "2026-07"


def test_convert_it_means_the_one_on_screen_not_the_earliest_in_the_thread():
    assert action_mod._grounded_period(
        "convert it to a pdf", _TWO_INVOICES, date(2026, 8, 12)
    ) == "2026-05"


@pytest.mark.parametrize(
    "plan",
    [
        {"tool": "request_booking", "arguments": {"instrument_id": "i-1"}},
        {"tool": "create_service_request", "arguments": {"summary": "usage review"}},
    ],
)
def test_a_complete_plan_for_another_tool_is_never_turned_into_a_date_question(plan):
    """"book me the cryo-EM next week for my usage" matched the usage_summary subject and
    was answered with "which month?", abandoning a request we had understood."""
    assert action_mod.require_document_period(
        plan, "book me the cryo-EM next week for my usage", ""
    ) == plan


@pytest.mark.parametrize(
    "message",
    [
        "what is the cancellation policy",
        "how do I book the confocal",
        "is the warm-up optional",
        "does the confocal need training",
    ],
)
def test_a_short_question_after_a_clarification_is_not_hijacked(message):
    """Punctuation is not the test people think it is: five words, no question mark, and
    plainly not an answer to "which month?"."""
    history = [{"q": "give me an invoice", "a": "Which period?", "type": "clarify",
                "route": "action"}]
    assert _answering_our_question({"message": message, "history": history}) is None
