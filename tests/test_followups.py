"""Multi-turn follow-ups, resolved in code rather than hoped for in a prompt.

Every case here is a real failure from the 2026-08-11 conversation review or a
neighbour of one. They are deterministic on purpose: the defects they cover reproduced
3/3, so a fix that only usually works is not a fix.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from server.agent.action import (
    apply_stated_duration,
    carry_forward_instrument,
    instruments_mentioned,
    stated_duration,
)
from server.agent.data import humanise_field_names, humanise_key

# --- the duration a user actually said --------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("then book it for 2 hours", timedelta(hours=2)),
        ("book it for 2 hrs", timedelta(hours=2)),
        ("2h please", timedelta(hours=2)),
        ("for 90 minutes", timedelta(minutes=90)),
        ("for 45 mins", timedelta(minutes=45)),
        ("30 min is enough", timedelta(minutes=30)),
        ("half an hour", timedelta(minutes=30)),
        ("just an hour", timedelta(hours=1)),
        ("a 2-hour slot", timedelta(hours=2)),
        ("for 1.5 hours", timedelta(minutes=90)),
        ("make it three hours", timedelta(hours=3)),
        ("book the confocal for four hours tomorrow", timedelta(hours=4)),
    ],
)
def test_a_stated_duration_is_read_exactly(message, expected):
    assert stated_duration(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "book it tomorrow",
        "with the existing booking data itself and 12 am",  # a time, not a length
        "book it at 9 am",
        "show me my bookings",
        "",
        "for 20 hours",       # over the 12h limit — the tool's refusal to word, not ours
        "for 0 hours",
    ],
)
def test_no_duration_is_invented(message):
    assert stated_duration(message) is None


def test_the_stated_duration_replaces_an_inherited_window():
    """The original defect: a full-day window survived "for 2 hours" and was then
    refused for exceeding twelve."""
    plan = {
        "tool": "request_booking",
        "arguments": {
            "instrument_id": "ins-confocal-c2",
            "starts_at": "2026-03-31T00:00:00Z",
            "ends_at": "2026-04-01T00:00:00Z",
            "account_code": "ACC-A1",
        },
    }
    out = apply_stated_duration(plan, "then book it for 2 hours")
    assert out["arguments"]["starts_at"] == "2026-03-31T00:00:00Z", "the start is settled"
    assert out["arguments"]["ends_at"] == "2026-03-31T02:00:00Z"


def test_a_window_that_already_matches_is_left_alone():
    plan = {
        "tool": "request_booking",
        "arguments": {"instrument_id": "ins-miseq", "starts_at": "2026-03-31T09:00:00Z",
                      "ends_at": "2026-03-31T11:00:00Z"},
    }
    assert apply_stated_duration(plan, "book it for 2 hours")["arguments"][
        "ends_at"] == "2026-03-31T11:00:00Z"


def test_duration_correction_never_touches_a_read_or_another_write():
    for tool in ("get_my_bookings", "create_service_request", "generate_document"):
        plan = {"tool": tool, "arguments": {"starts_at": "2026-03-31T00:00:00Z",
                                            "ends_at": "2026-04-01T00:00:00Z"}}
        assert apply_stated_duration(plan, "for 2 hours")["arguments"][
            "ends_at"] == "2026-04-01T00:00:00Z"


@pytest.mark.parametrize(
    "arguments",
    [
        {},                                                   # nothing to correct
        {"starts_at": "not-a-date", "ends_at": "2026-04-01T00:00:00Z"},
        {"starts_at": "2026-03-31T00:00:00Z"},                # no end at all
        {"starts_at": None, "ends_at": None},
    ],
)
def test_an_unparseable_plan_is_left_for_the_tool_to_reject(arguments):
    plan = {"tool": "request_booking", "arguments": dict(arguments)}
    assert apply_stated_duration(plan, "for 2 hours")["arguments"] == arguments


# --- which instrument the conversation is about -----------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("book Confocal C2", ["ins-confocal-c2"]),
        ("book the C2", ["ins-confocal-c2"]),
        ("ins-confocal-c2 please", ["ins-confocal-c2"]),
        ("is LS7 free?", ["ins-lightsheet"]),
        ("book MiSeq M3 for 2 hours", ["ins-miseq"]),
        ("make it 3 hours instead", []),
        ("for 2 hours", []),
        ("book it", []),
    ],
)
def test_instruments_are_recognised_the_way_people_name_them(text, expected):
    rows = [("ins-confocal-c2", "Confocal C2"), ("ins-confocal-c3", "Confocal C3"),
            ("ins-lightsheet", "Light Sheet LS7"), ("ins-miseq", "MiSeq M3")]
    assert instruments_mentioned(text, rows) == expected


def test_c2_and_c3_are_not_confused():
    rows = [("ins-confocal-c2", "Confocal C2"), ("ins-confocal-c3", "Confocal C3")]
    assert instruments_mentioned("book C3", rows) == ["ins-confocal-c3"]


def test_mentions_come_back_in_the_order_they_appear():
    rows = [("ins-miseq", "MiSeq M3"), ("ins-confocal-c2", "Confocal C2")]
    assert instruments_mentioned(
        "C2 was busy so I used MiSeq M3", rows
    ) == ["ins-confocal-c2", "ins-miseq"]


@pytest.mark.tools
def test_a_followup_books_the_instrument_under_discussion():
    """"make it 3 hours instead" proposed a different machine entirely."""
    history = ("user: is Confocal C2 free on 31 March?\n"
               "assistant: Confocal C2 is free between 08:00 and 20:00 UTC.")
    plan = {"tool": "request_booking",
            "arguments": {"instrument_id": "ins-em-titan",
                          "starts_at": "2026-03-31T08:00:00Z",
                          "ends_at": "2026-03-31T11:00:00Z"}}
    out = carry_forward_instrument(plan, "make it 3 hours instead", history)
    assert out["arguments"]["instrument_id"] == "ins-confocal-c2"


@pytest.mark.tools
def test_the_instrument_named_now_beats_the_one_named_earlier():
    history = "assistant: Confocal C2 is free between 08:00 and 20:00 UTC."
    plan = {"tool": "request_booking",
            "arguments": {"instrument_id": "ins-confocal-c2",
                          "starts_at": "2026-03-31T08:00:00Z",
                          "ends_at": "2026-03-31T10:00:00Z"}}
    out = carry_forward_instrument(plan, "actually book the MiSeq M3 instead", history)
    assert out["arguments"]["instrument_id"] == "ins-miseq"


@pytest.mark.tools
def test_the_most_recent_instrument_wins_when_several_were_discussed():
    history = ("assistant: Confocal C2 is busy.\n"
               "user: what about the MiSeq?\n"
               "assistant: MiSeq M3 is free from 08:00.")
    plan = {"tool": "request_booking",
            "arguments": {"instrument_id": "ins-em-titan",
                          "starts_at": "2026-03-31T08:00:00Z",
                          "ends_at": "2026-03-31T10:00:00Z"}}
    out = carry_forward_instrument(plan, "book it for 2 hours", history)
    assert out["arguments"]["instrument_id"] == "ins-miseq"


@pytest.mark.tools
def test_two_instruments_in_one_message_are_left_ambiguous():
    """Guessing between them is worse than letting the user say which."""
    plan = {"tool": "request_booking", "arguments": {"instrument_id": "ins-miseq"}}
    out = carry_forward_instrument(plan, "C2 or MiSeq M3, whichever is free", "")
    assert out["arguments"]["instrument_id"] == "ins-miseq", "unchanged, not guessed"


@pytest.mark.tools
def test_with_no_instrument_anywhere_the_planner_is_left_alone():
    plan = {"tool": "request_booking", "arguments": {"instrument_id": "ins-miseq"}}
    assert carry_forward_instrument(plan, "book something", "")[
        "arguments"]["instrument_id"] == "ins-miseq"


# --- field names never reach the reader -------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("requested_window_free", "the requested window is free"),
        ("conflicting_bookings", "conflicting bookings"),
        ("starts_at", "start time"),
        ("unavailable_reason", "why it cannot be booked"),
        ("some_unmapped_column", "some unmapped column"),
    ],
)
def test_keys_are_relabelled_in_english(key, expected):
    assert humanise_key(key) == expected


def test_a_leaked_field_name_is_rewritten():
    """Verbatim from the transcript that started this."""
    draft = ("Light Sheet LS7 is available. requested_window_free is False. "
             "Conflicting bookings are 0.")
    out = humanise_field_names(draft, {"requested_window_free", "conflicting_bookings"})
    assert "requested_window_free" not in out
    assert "the requested window is free is False" in out


def test_a_value_that_looks_like_a_field_name_is_left_exactly_as_stored():
    """Rule 4 — values are spelled the way the record spells them. That outranks tidiness."""
    draft = "The request is in_progress and the template is seq_run."
    assert humanise_field_names(draft, {"status", "template_id"}) == draft


def test_only_keys_present_in_this_result_are_rewritten():
    draft = "The booking starts_at 09:00 and the sample was collected_at 08:00."
    out = humanise_field_names(draft, {"starts_at"})
    assert "start time" in out
    assert "collected_at" in out, "not a column here, so not ours to rewrite"


def test_prose_without_field_names_is_untouched():
    draft = "Alice has 20 bookings, all completed, on account ACC-A1."
    assert humanise_field_names(draft, {"account_code", "status"}) == draft


# --- a plan that names a scope that is not a scope ---------------------------------


def test_a_bad_usage_scope_with_no_id_reads_as_the_caller():
    """"How many hours is that in total?" planned scope='total' about one time in three."""
    from server.agent.data import _normalise_plan

    plan = _normalise_plan(
        {"tool": "get_usage_records", "arguments": {"scope": "total", "month": "2026-03"}}
    )
    assert plan["arguments"]["scope"] == "user"


@pytest.mark.parametrize(
    ("subject", "expected"),
    [("u-alice", "user"), ("lab-a", "lab"), ("ins-miseq", "instrument")],
)
def test_the_id_says_which_scope_it_is(subject, expected):
    """The real failure was scope='tracked' with id='u-alice' — the id already said."""
    from server.agent.data import _normalise_plan

    plan = _normalise_plan(
        {"tool": "get_usage_records", "arguments": {"scope": "tracked", "id": subject}}
    )
    assert plan["arguments"]["scope"] == expected


def test_an_unfamiliar_id_is_left_for_the_tool_to_reject():
    """Nothing is guessed: an id we cannot read means the tool's error stands."""
    from server.agent.data import _normalise_plan

    plan = _normalise_plan(
        {"tool": "get_usage_records", "arguments": {"scope": "everything", "id": "xyz-1"}}
    )
    assert plan["arguments"]["scope"] == "everything"


def test_a_valid_scope_is_never_touched():
    from server.agent.data import _normalise_plan

    for scope in ("user", "lab", "instrument"):
        plan = _normalise_plan(
            {"tool": "get_usage_records", "arguments": {"scope": scope}}
        )
        assert plan["arguments"]["scope"] == scope


# --- naming an instrument the way people actually do -------------------------------


@pytest.mark.tools
@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("the light sheet", ["ins-lightsheet"]),
        ("book the cryo-EM", ["ins-em-titan"]),
        ("the cryo em", ["ins-em-titan"]),          # hyphen optional
        ("on the miseq", ["ins-miseq"]),
        ("the spinning disk", ["ins-spinning-disk"]),
        ("the orbitrap", ["ins-orbitrap"]),
        ("book it", []),
        ("for 2 hours", []),
    ],
)
def test_an_instrument_kind_is_recognised_without_its_model_number(said, expected):
    """Nobody says "Confocal C2" twice. They say "the confocal"."""
    from server.agent.action import _instrument_rows, instrument_family_mentioned

    assert instrument_family_mentioned(said, _instrument_rows()) == expected


@pytest.mark.tools
def test_back_to_the_confocal_does_not_book_the_light_sheet():
    """The real failure: "OK, back to the confocal. Book it..." proposed the Light Sheet,
    which was under maintenance, because the exact matcher saw no instrument in the
    message and fell through to the last one mentioned anywhere in the conversation."""
    history = "assistant: Light Sheet LS7 and Confocal C2."
    plan = {"tool": "request_booking", "arguments": {"instrument_id": "ins-lightsheet"}}
    out = carry_forward_instrument(
        plan, "OK, back to the confocal. Book it on 5 April 2027 from 10am", history
    )
    assert out["arguments"]["instrument_id"] == "ins-confocal-c2"


@pytest.mark.tools
def test_a_kind_naming_two_instruments_with_nothing_to_choose_by_asks():
    """"BOOK THE CONFOCAL NOW!!!" silently picked C3. They bill at different rates."""
    plan = {"tool": "request_booking", "arguments": {"instrument_id": "ins-confocal-c3"}}
    out = carry_forward_instrument(plan, "BOOK THE CONFOCAL NOW!!!", "")
    assert out["tool"] is None
    assert "Confocal C2" in out["ask"] and "Confocal C3" in out["ask"]


# --- one date means that day -------------------------------------------------------


def test_availability_given_one_date_reads_it_as_that_whole_day():
    """"Is the MiSeq free on 6 April 2027?" asked the user for an end date they had no
    reason to think about."""
    from server.agent.data import _normalise_plan

    plan = _normalise_plan({
        "tool": "check_availability",
        "arguments": {"instrument_id": "ins-miseq", "date_from": "2027-04-06"},
    })
    assert plan["arguments"]["date_to"] == "2027-04-06"


def test_availability_given_only_an_end_date_is_repaired_the_same_way():
    from server.agent.data import _normalise_plan

    plan = _normalise_plan({
        "tool": "check_availability",
        "arguments": {"instrument_id": "ins-miseq", "date_to": "2027-04-06"},
    })
    assert plan["arguments"]["date_from"] == "2027-04-06"


def test_a_complete_availability_window_is_untouched():
    from server.agent.data import _normalise_plan

    args = {"instrument_id": "ins-miseq", "date_from": "2027-04-06", "date_to": "2027-04-08"}
    assert _normalise_plan({"tool": "check_availability", "arguments": dict(args)})[
        "arguments"] == args


# --- errors a person can read ------------------------------------------------------


@pytest.mark.tools
def test_a_missing_argument_is_named_in_words_not_schema():
    """"That lookup needs date to. Say which date to you mean." reads like a broken
    machine, which is worse than the TypeError it replaced."""
    from server.mcp import tools as T
    from server.mcp.errors import ToolError

    ctx = __import__("server.auth", fromlist=["Ctx"]).Ctx(
        user_id="u-alice", name="Alice", role="user",
        lab_ids=("lab-a",), facility_ids=(), raw={},
    )
    with pytest.raises(ToolError) as exc:
        T.call(ctx, "get_billing_summary", {})
    assert exc.value.message == "That lookup needs an account code and a period."
    assert "_" not in exc.value.message and "_" not in exc.value.hint


def test_a_flattened_nested_key_reads_as_the_nesting_meant():
    """`training: {confocal: true}` flattens to `training_confocal`, and the generic
    rule made the model answer "You are trained on training confocal"."""
    assert humanise_key("training_confocal") == "trained on confocal"
    assert humanise_key("training_biosafety-2") == "trained on biosafety-2"
    assert humanise_key("training") == "training"


def test_a_number_inside_a_column_name_is_quotable():
    """A training level called biosafety-2 is a label. Counting its 2 as an unsupported
    figure threw away a correct answer in favour of the raw table."""
    from server.agent.data import verify_numbers

    rows = [{"training_biosafety-2": True, "training_confocal": True}]
    assert verify_numbers("You hold biosafety-2 and confocal training.", rows, "") == []


def test_an_invented_number_is_still_caught():
    """The guard must not have been widened into uselessness."""
    from server.agent.data import verify_numbers

    rows = [{"training_biosafety-2": True}]
    assert verify_numbers("You have 47 trainings on record.", rows, "") == ["47"]
