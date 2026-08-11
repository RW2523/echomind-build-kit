"""M5 verification — the agent end to end (pytest -m agent).

Covers the three outcomes PLAN.md names for this milestone, the chat-level tier test
spec 05 defers to here, and the escalation stub's no-egress guarantee.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from server.agent import escalation
from server.agent.data import column_totals, verify_numbers
from server.agent.graph import resume_turn, run_turn
from server.agent.responses import SCOPE_MESSAGE
from server.db import owner_session, session_scope
from server.mcp import actions as actions_mod

pytestmark = pytest.mark.agent


def _thread() -> str:
    return f"thr-test-{uuid.uuid4().hex[:8]}"


# --- data branch: values must match the seeded rows exactly --------------------------


@pytest.fixture(scope="module")
def billing_turn(ctxs):
    return run_turn("Why was lab A charged $412 in March?", ctxs["asha"], _thread())


def test_billing_question_returns_a_rows_answer(billing_turn):
    assert billing_turn.response_type == "rows_answer"
    assert billing_turn.route == "data"
    assert billing_turn.rows


def test_billing_answer_states_the_seeded_value_exactly(billing_turn, db):
    seeded = db.execute(
        text(
            """SELECT sum(amount) FROM reporting.v_billing_lines
               WHERE lab_id = 'lab-a' AND period = '2026-03' AND instrument = 'Confocal C2'"""
        )
    ).scalar_one()
    assert float(seeded) == 412.00
    assert "412.00" in billing_turn.text


def test_every_number_in_the_reply_exists_in_the_returned_rows(billing_turn):
    """Golden rule 1, asserted directly: no invented values."""
    offenders = verify_numbers(
        billing_turn.text, billing_turn.rows, "Why was lab A charged $412 in March?"
    )
    assert offenders == []


def test_data_answer_is_lab_scoped_for_a_pi(billing_turn):
    if billing_turn.executed_sql:
        assert "lab-a" in billing_turn.executed_sql


def test_column_totals_are_computed_not_guessed():
    rows = [{"amount": 252.00}, {"amount": 160.00}]
    assert float(column_totals(rows)["amount"]) == 412.00


def test_verifier_rejects_a_number_absent_from_the_rows():
    rows = [{"amount": 252.00}, {"amount": 160.00}]
    assert verify_numbers("The total is $999.00.", rows, "total?") == ["999.00"]


def test_verifier_accepts_the_true_total():
    rows = [{"amount": 252.00}, {"amount": 160.00}]
    assert verify_numbers("The total is $412.00.", rows, "total?") == []


# --- action branch: propose -> approve -> execute -> audit ---------------------------


@pytest.fixture
def booking_thread(ctxs):
    """A proposed-but-undecided booking, cleaned up afterwards."""
    thread_id = _thread()
    slot = "2027-09-14T09:00:00Z"
    end = "2027-09-14T11:00:00Z"
    response = run_turn(
        f"Book Confocal C2 from {slot} to {end} on account ACC-A1", ctxs["alice"], thread_id
    )
    yield thread_id, response, slot
    # Owner, not the application: creating a booking is something echomind_app may do,
    # removing one is not.
    with owner_session() as s:
        s.execute(text("DELETE FROM infinity.bookings WHERE starts_at = :t"), {"t": slot})


def test_booking_request_creates_a_pending_action(booking_thread):
    _, response, slot = booking_thread
    assert response.response_type == "approval_request"
    assert response.pending_action["status"] == "pending"
    with session_scope() as s:
        written = s.execute(
            text("SELECT count(*) FROM infinity.bookings WHERE starts_at = :t"), {"t": slot}
        ).scalar_one()
    assert written == 0, "nothing may reach infinity.* before approval"


def test_the_proposal_is_created_exactly_once(booking_thread, ctxs):
    """Regression: an interrupt re-runs its node, which once proposed the action twice."""
    _, _response, _ = booking_thread
    with session_scope() as s:
        count = s.execute(
            text(
                "SELECT count(*) FROM echomind.actions "
                "WHERE tool = 'request_booking' AND user_id = 'u-alice' AND status = 'pending' "
                "AND payload->>'starts_at' LIKE '2027-09-14%'"
            )
        ).scalar_one()
    assert count == 1


def test_approval_executes_and_audit_shows_both_events(booking_thread, ctxs):
    _thread_id, response, slot = booking_thread
    action_id = response.pending_action["action_id"]

    outcome = actions_mod.approve(ctxs["alice"], action_id)
    assert outcome["status"] == "executed"

    with session_scope() as s:
        booking = s.execute(
            text("SELECT id, status, user_id FROM infinity.bookings WHERE starts_at = :t"),
            {"t": slot},
        ).mappings().first()
        events = [
            e[0] for e in s.execute(
                text("SELECT event FROM echomind.audit_log WHERE action_id = :a ORDER BY id"),
                {"a": action_id},
            ).all()
        ]

    assert booking is not None
    assert booking["user_id"] == "u-alice"
    assert booking["status"] == "requested"
    assert "proposed" in events and "approved" in events and "executed" in events


def test_approval_resumes_the_conversation_with_the_result(booking_thread, ctxs):
    thread_id, response, _ = booking_thread
    action_id = response.pending_action["action_id"]
    actions_mod.approve(ctxs["alice"], action_id)

    resumed = resume_turn(thread_id, actions_mod.get_action(action_id))
    assert resumed is not None
    assert resumed.response_type == "answer"
    # The confirmation quotes the stored result, not the model's memory of the request.
    result = actions_mod.get_action(action_id)["result"]
    assert result["booking_id"] in resumed.text


def test_decline_resumes_politely_and_changes_nothing(booking_thread, ctxs):
    thread_id, response, slot = booking_thread
    action_id = response.pending_action["action_id"]

    actions_mod.decline(ctxs["alice"], action_id)
    resumed = resume_turn(thread_id, actions_mod.get_action(action_id))

    assert resumed is not None
    assert "declined" in resumed.text.lower()
    with session_scope() as s:
        written = s.execute(
            text("SELECT count(*) FROM infinity.bookings WHERE starts_at = :t"), {"t": slot}
        ).scalar_one()
    assert written == 0

    with session_scope() as s:
        events = [
            e[0] for e in s.execute(
                text("SELECT event FROM echomind.audit_log WHERE action_id = :a ORDER BY id"),
                {"a": action_id},
            ).all()
        ]
    assert events == ["proposed", "declined"]


# --- scope ---------------------------------------------------------------------------


def test_out_of_scope_prompt_gets_the_scope_message(ctxs):
    response = run_turn("Who won the 2019 Nobel prize in physics?", ctxs["alice"], _thread())
    assert response.response_type == "scope"
    assert response.text == SCOPE_MESSAGE
    assert "Nobel" not in response.text


def test_smalltalk_gets_a_brief_reply(ctxs):
    response = run_turn("hello", ctxs["alice"], _thread())
    assert response.response_type == "smalltalk"


# --- chat-level tier enforcement (the test spec 05 defers to M5) ----------------------


def test_bob_asking_for_alices_bookings_is_refused_not_answered(ctxs):
    response = run_turn("show me alice's bookings", ctxs["bob"], _thread())
    assert response.response_type == "redirect"
    assert "do not have access" in response.text.lower()
    assert not response.rows, "a denial must not carry data of any kind"


def test_bob_asking_about_a_lab_a_protocol_gets_a_redirect(ctxs):
    response = run_turn(
        "What primary antibody dilution does the Patel lab house protocol use?",
        ctxs["bob"],
        _thread(),
    )
    assert response.response_type == "redirect"
    assert "1:400" not in response.text


# --- escalation stub: present, wired, and silent --------------------------------------


def test_escalation_never_routes_when_disabled():
    from server.config import settings

    assert settings.escalation_enabled is False
    for score in (0.0, 0.29, 0.30, 0.44, 0.45, 0.9):
        assert escalation.should_escalate(score) is False


def test_escalation_refuses_to_make_a_request_when_disabled():
    with pytest.raises(RuntimeError, match="disabled"):
        escalation.escalate("any question", "any context")


def test_pseudonymizer_removes_identifiers():
    text_in = (
        "u-alice booked Confocal C2 on ACC-A1 for lab-a; contact alice@example.edu "
        "about sample BC100001."
    )
    result = escalation.pseudonymize(text_in, names=["Alice Nguyen"])
    for secret in ("u-alice", "ACC-A1", "lab-a", "alice@example.edu", "BC100001"):
        assert secret not in result.text
    assert result.restore(result.text) == text_in


def test_pseudonymizer_is_stable_for_repeated_identifiers():
    result = escalation.pseudonymize("u-alice and u-alice and u-bob")
    assert result.text.count("<USER_1>") == 2
    assert "<USER_2>" in result.text
