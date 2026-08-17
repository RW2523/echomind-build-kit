"""Multi-turn follow-ups, resolved in code rather than hoped for in a prompt.

Every case here is a real failure from the 2026-08-11 conversation review or a
neighbour of one. They are deterministic on purpose: the defects they cover reproduced
3/3, so a fix that only usually works is not a fix.
"""

from __future__ import annotations

from datetime import datetime, timedelta

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
def test_two_instruments_in_one_message_are_asked_about():
    """Guessing between them is worse than letting the user say which — so it asks,
    naming both, rather than shipping the planner's arbitrary pick."""
    plan = {"tool": "request_booking", "arguments": {"instrument_id": "ins-miseq"}}
    out = carry_forward_instrument(plan, "C2 or MiSeq M3, whichever is free", "")
    assert out["tool"] is None
    assert "Confocal C2" in out["ask"] and "MiSeq M3" in out["ask"]


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


# --- defects found by the 2026-08-12 adversarial workflow --------------------------

from decimal import Decimal  # noqa: E402

from server.agent.action import (  # noqa: E402
    apply_relative_date,
    relative_date_target,
)
from server.agent.data import (  # noqa: E402
    _ASKS_FOR_AN_EXTREME_RE,
    _QUESTION_STATES_A_DATE_RE,
    _display,
    _invented_record_id,
    drop_a_label_restated_as_a_value,
    _mentions_unsupported_lab,
    _month_stated_in,
    _named_record_absent_from,
    _plan_at_the_callers_own_scope,
    _plan_with_the_month_the_caller_named,
    _plan_without_an_invented_window,
    _rows_from_tool_result,
    start_with_a_capital,
)


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        (Decimal("0E-20"), "0.00"),               # a NUMERIC zero, not "0E-20"
        (Decimal("1.9750000000000000"), "1.98"),  # an AVG artifact, not 16 digits
        (Decimal("412.00"), "412.00"),            # money keeps its cents
        (Decimal("5514.50"), "5514.50"),
        (1.806666666, "1.81"),
        (20, "20"),                               # a count is an integer, untouched
        (0, "0"),
        (True, "yes"),                            # a boolean is English, not "True"
        (False, "no"),
        ("in_prep", "in_prep"),                   # a stored string is verbatim
    ],
)
def test_values_are_displayed_as_people_read_them(value, shown):
    assert _display(value) == shown


def test_availability_result_never_exposes_its_schema_as_columns():
    """The maintenance branch dumped instrument_id, requested_window_free, bookable, ...
    as raw table headers. The free windows are the only rows that belong in the table."""
    unbookable = {
        "instrument": {"id": "ins-lightsheet", "name": "Light Sheet LS7"},
        "instrument_name": "Light Sheet LS7", "bookable": False,
        "unavailable_reason": "Light Sheet LS7 is maintenance and cannot be booked",
        "requested_window_free": False, "conflicting_bookings": 0,
        "free_slots": [], "busy": [],
    }
    rows, columns, scalars = _rows_from_tool_result(unbookable, "check_availability")
    assert rows == [] and columns == []
    assert "requested_window_free" not in columns
    assert scalars["bookable"] is False and "unavailable_reason" in scalars

    bookable = {**unbookable, "bookable": True, "requested_window_free": True,
                "free_slots": [{"starts_at": "2027-04-08T08:00:00Z",
                                "ends_at": "2027-04-08T20:00:00Z"}]}
    rows, columns, _ = _rows_from_tool_result(bookable, "check_availability")
    # free_from/free_until, not starts_at/ends_at: with the booking-shaped keys the
    # generator read a free slot back as a booking. The meaning rides in the key.
    assert columns == ["free_from", "free_until"]
    assert rows[0]["free_from"] == "2027-04-08T08:00:00Z"


def test_an_empty_collection_is_no_rows_not_a_row_about_the_collection():
    """"count is 0. bookings is none." — the envelope's own field names, read back to a
    caller whose previous turn had just listed 17 bookings. An empty list is zero rows."""
    empty = {"user_id": "u-bob", "count": 0, "bookings": []}
    rows, columns, scalars = _rows_from_tool_result(empty, "get_my_bookings")
    assert rows == [] and columns == []
    # Nothing left to speak: the honest "I found no records matching that" is the answer,
    # not "count is 0" and not the caller's own id read back to them.
    assert scalars == {}


def test_an_empty_collection_still_reports_what_explains_it():
    """Emptiness with a reason keeps the reason — only the bookkeeping is dropped."""
    empty = {"account_code": "ACC-B1", "period": "2026-05", "count": 0, "lines": []}
    rows, columns, scalars = _rows_from_tool_result(empty, "get_billing_summary")
    assert rows == [] and columns == []
    assert scalars == {"account_code": "ACC-B1", "period": "2026-05"}


@pytest.mark.parametrize(
    ("question", "arguments", "widened"),
    [
        # The transcript: 17 bookings, then none, because "latest" became a date.
        ("show me the results", {"date_from": "2026-03-25", "date_to": "2026-03-25"}, True),
        ("show me the results of my latest booking", {"date_from": "2026-03-01"}, True),
        ("what was my most recent booking", {"date_from": "2026-08-01"}, True),
        # A window the caller asked for is theirs: "nothing in March" is a true answer,
        # and widening it would answer a question nobody put.
        ("what did I book in 2026-03", {"date_from": "2026-03-01"}, False),
        ("my bookings in March", {"date_from": "2026-03-01"}, False),
        ("my bookings in May", {"date_from": "2026-05-01"}, False),
        # "May" as the verb is not a date, so this one still widens.
        ("may I see my latest booking", {"date_from": "2026-08-01"}, True),
        ("did I book anything last week", {"date_from": "2026-08-09"}, False),
        ("anything booked since April", {"date_from": "2026-04-01"}, False),
        ("what am I booked on today", {"date_from": "2026-08-16"}, False),
        # Nothing to undo.
        ("show my bookings", {}, False),
    ],
)
def test_only_an_unasked_for_date_window_is_taken_back_off(question, arguments, widened):
    plan = {"mode": "tool", "tool": "get_my_bookings", "arguments": arguments}
    result = _plan_without_an_invented_window(plan, question)
    assert (result is not None) == widened
    if widened:
        assert result["arguments"] == {}
        assert result["tool"] == "get_my_bookings"


@pytest.mark.parametrize(
    ("question", "is_extreme"),
    [
        ("show me the results of my latest booking", True),
        ("what was my most recent booking", True),
        ("my last booking", True),
        ("what is my next booking", True),
        # Dates, not orderings — and caught as dates before the ordering rule is reached.
        ("did I book anything last week", False),
        ("my bookings last month", False),
        ("show me the results", False),
        ("show my bookings", False),
    ],
)
def test_a_superlative_is_told_apart_from_a_date(question, is_extreme):
    """"last week" is a window the caller chose; "my last booking" is an ordering over
    every booking they have. Only the second one may drop the planner's range."""
    asks = bool(_ASKS_FOR_AN_EXTREME_RE.search(question))
    dated = bool(_QUESTION_STATES_A_DATE_RE.search(question))
    assert (asks and not dated) == is_extreme


@pytest.mark.parametrize(
    ("arguments", "question", "invented"),
    [
        # The transcript: an id nobody gave, which came back as a flat access denial.
        ({"sample_id": "s-12345"}, "where is my sample?", "sample_id"),
        ({"booking_id": "booking-123"}, "cancel my next booking", "booking_id"),
        ({"barcode": "SMP-0001"}, "track my sample", "barcode"),
        # An id the caller actually gave, in either turn's spelling.
        ({"sample_id": "smp-1042"}, "where is sample smp-1042?", None),
        ({"barcode": "SMP-0001"}, "track barcode smp-0001 please", None),
        # Ids the system resolves rather than invents are not record identifiers.
        ({"instrument_id": "ins-confocal-c2"}, "is Confocal C2 free?", None),
        ({"account_code": "ACC-A1"}, "what is on my invoice?", None),
        ({}, "show my bookings", None),
    ],
)
def test_an_identifier_the_caller_never_gave_is_asked_for(arguments, question, invented):
    plan = {"mode": "tool", "tool": "track_sample", "arguments": arguments}
    assert _invented_record_id(plan, question) == invented


def test_an_identifier_given_earlier_in_the_thread_counts_as_given():
    plan = {"mode": "tool", "tool": "track_sample", "arguments": {"barcode": "SMP-7"}}
    assert _invented_record_id(plan, "where is it now?", "user: track SMP-7") is None


@pytest.mark.parametrize(
    ("question", "arguments", "narrowed"),
    [
        # "I" — the wide reading was refused, and the caller's own is untried.
        ("when did I last use the Cryo-EM?", {"scope": "instrument", "id": "ins-em"}, True),
        ("how many hours have we used?", {"scope": "lab", "id": "lab-a"}, True),
        # Not first person: the refusal is the right answer and stands.
        ("how many hours did lab B use?", {"scope": "lab", "id": "lab-b"}, False),
        ("how much has the MiSeq been used?", {"scope": "instrument", "id": "ins-m3"}, False),
        # Already the caller's own scope — nothing narrower to try.
        ("how many hours have I used?", {"scope": "user"}, False),
    ],
)
def test_a_refused_first_person_question_is_retried_at_the_callers_own_scope(
    question, arguments, narrowed
):
    plan = {"mode": "tool", "tool": "get_usage_records", "arguments": arguments}
    result = _plan_at_the_callers_own_scope(plan, question)
    assert (result is not None) == narrowed
    if narrowed:
        assert result["arguments"] == {"scope": "user"}


@pytest.mark.parametrize(
    ("question", "rows", "absent"),
    [
        # The transcript: a real status for a booking nobody asked about.
        ("what is the status of booking bk-9999?", [{"id": "bk-0071"}], "bk-9999"),
        ("status of bk-9999 and bk-8888?", [{"id": "bk-0071"}], "bk-8888"),
        # Found: answer normally.
        ("what is the status of booking bk-0071?", [{"id": "bk-0071"}], None),
        # One of two found is a partial answer worth giving.
        ("bk-0071 and bk-9999?", [{"id": "bk-0071"}], None),
        # No id named at all.
        ("show my bookings", [{"id": "bk-0071"}], None),
    ],
)
def test_an_id_the_caller_named_is_never_answered_with_a_different_record(
    question, rows, absent
):
    plan = {"mode": "tool", "tool": "get_my_bookings", "arguments": {}}
    assert _named_record_absent_from(rows, plan, question) == absent


def test_an_invoice_line_missing_a_booking_id_is_not_called_absent():
    """get_billing_summary rows do not carry booking ids, so "not in these rows" would
    not mean "not on record" — the check stays away from tools that cannot know."""
    plan = {"mode": "tool", "tool": "get_billing_summary", "arguments": {}}
    rows = [{"description": "Cryo-EM Titan usage", "amount": "290.00"}]
    assert _named_record_absent_from(rows, plan, "what did bk-0071 cost?") is None


@pytest.mark.parametrize(
    ("draft", "rows", "opened"),
    [
        ("the status is requested.", [{"id": "bk-1"}], "The status is requested."),
        ("the imaging core's opening hours are 08:00-20:00.", [],
         "The imaging core's opening hours are 08:00-20:00."),
        ("You have 17 bookings.", [], "You have 17 bookings."),
        ("", [], ""),
        # Rule 4 outranks tidiness: a reply opening ON a stored value keeps its spelling.
        ("in_prep is where it has got to.", [{"status": "in_prep"}],
         "in_prep is where it has got to."),
        ("bk-0071 is the latest.", [{"id": "bk-0071"}], "bk-0071 is the latest."),
    ],
)
def test_an_answer_opens_with_a_capital_unless_it_opens_on_a_value(draft, rows, opened):
    assert start_with_a_capital(draft, rows, {}) == opened


def test_zero_conflicts_is_not_a_finding_worth_a_sentence():
    """"Confocal C2 is free. Conflicting bookings are 0." — a label and a value tacked
    onto a complete answer. A non-zero count is the answer and stays."""
    free = {"instrument_name": "Confocal C2", "bookable": True,
            "requested_window_free": True, "conflicting_bookings": 0, "free_slots": []}
    _, _, scalars = _rows_from_tool_result(free, "check_availability")
    assert "conflicting_bookings" not in scalars

    busy = {**free, "requested_window_free": False, "conflicting_bookings": 3}
    _, _, scalars = _rows_from_tool_result(busy, "check_availability")
    assert scalars["conflicting_bookings"] == 3


@pytest.mark.parametrize(
    ("question", "month"),
    [
        ("what were my usage hours in March 2026?", "2026-03"),
        ("my usage for 2026-03", "2026-03"),
        ("what did I spend in December 2025?", "2025-12"),
        # A bare month could be five months back or seven forward. Guessing answers a
        # question nobody asked.
        ("what were my usage hours in March?", None),
        ("how many hours have I used?", None),
    ],
)
def test_only_an_unambiguous_month_is_read_out_of_the_question(question, month):
    assert _month_stated_in(question) == month


def test_a_month_the_caller_named_is_actually_filtered_on():
    """All 17 usage rows came back and ONE March row was quoted as the March total:
    "scheduled hours are 0.00" for a month whose scheduled hours were 24.50."""
    plan = {"mode": "tool", "tool": "get_usage_records", "arguments": {"scope": "user"}}
    out = _plan_with_the_month_the_caller_named(plan, "my usage hours in March 2026?")
    assert out["arguments"] == {"scope": "user", "month": "2026-03"}

    # get_billing_summary spells the same idea "period".
    plan = {"mode": "tool", "tool": "get_billing_summary",
            "arguments": {"account_code": "ACC-A1"}}
    out = _plan_with_the_month_the_caller_named(plan, "my invoice for March 2026")
    assert out["arguments"]["period"] == "2026-03"

    # A month the planner already supplied is left exactly as it is.
    plan = {"mode": "tool", "tool": "get_usage_records",
            "arguments": {"scope": "user", "month": "2026-02"}}
    assert _plan_with_the_month_the_caller_named(plan, "usage in March 2026") is None


def test_a_month_the_planner_invented_for_a_superlative_comes_off_too():
    """"When did I last use the Cryo-EM?" was planned with month=2026-08, matched no
    rows, and reported "scheduled hours of 0, tracked hours of 0" — a usage figure for a
    month the caller never mentioned, about an instrument they last used in January."""
    plan = {"mode": "tool", "tool": "get_usage_records",
            "arguments": {"scope": "user", "month": "2026-08"}}
    out = _plan_without_an_invented_window(plan, "when did I last use the Cryo-EM?")
    assert out["arguments"] == {"scope": "user"}

    # A month the caller did name stays put.
    assert _plan_without_an_invented_window(
        plan, "what did I use in August 2026?"
    ) is None


def test_a_required_period_is_never_stripped():
    """get_billing_summary cannot run without a period, so widening would break it."""
    plan = {"mode": "tool", "tool": "get_billing_summary",
            "arguments": {"account_code": "ACC-A1", "period": "2026-03"}}
    assert _plan_without_an_invented_window(plan, "what was my last invoice?") is None


@pytest.mark.parametrize(
    ("draft", "rows", "scalars", "kept"),
    [
        # The transcript: the answer, then the same fact in the schema's words.
        ('You are trained on the confocal. The record shows "trained on confocal" is yes.',
         [{"training_confocal": True}], {}, "You are trained on the confocal."),
        ("Confocal C2 is free on 2 April 2027. Conflicting bookings are 0.",
         [], {"conflicting_bookings": 0}, "Confocal C2 is free on 2 April 2027."),
        # Never the last sentence standing: an awkward fact beats no answer.
        ('The record shows "trained on confocal" is yes.', [{"training_confocal": True}],
         {}, 'The record shows "trained on confocal" is yes.'),
        # A real value is not a label restatement, however it is phrased.
        ("The status is requested. The account code is ACC-A1.",
         [{"status": "requested", "account_code": "ACC-A1"}], {},
         "The status is requested. The account code is ACC-A1."),
        # A label this result does not have is somebody's prose, and is left alone.
        ("It is free. Widget count is 0.", [{"status": "x"}], {},
         "It is free. Widget count is 0."),
        ("You have 17 bookings. All are completed.", [{"status": "completed"}], {},
         "You have 17 bookings. All are completed."),
    ],
)
def test_a_label_read_back_with_its_value_is_dropped(draft, rows, scalars, kept):
    assert drop_a_label_restated_as_a_value(draft, rows, scalars) == kept


def test_an_availability_window_is_never_widened():
    """check_availability's window IS the question. No free slots on Thursday is an
    answer; re-asking without the Thursday answers something else entirely."""
    plan = {"mode": "tool", "tool": "check_availability",
            "arguments": {"instrument_id": "ins-c2", "date_from": "2027-04-08",
                          "date_to": "2027-04-08"}}
    assert _plan_without_an_invented_window(plan, "is Confocal C2 free") is None


def test_a_populated_collection_is_unaffected_by_the_empty_case():
    populated = {"user_id": "u-bob", "count": 1, "bookings": [
        {"id": "bk-0071", "instrument": "MALDI-TOF R2", "status": "completed"}]}
    rows, columns, scalars = _rows_from_tool_result(populated, "get_my_bookings")
    assert columns == ["id", "instrument", "status"]
    assert rows[0]["id"] == "bk-0071"
    # The count stays a result fact about the whole set, never merged into the rows.
    assert scalars["count"] == 1


def test_relative_month_phrase_resolves_against_today():
    from datetime import date
    today = date(2026, 8, 12)
    assert relative_date_target("book it next month", today) == ("month", (2026, 9))
    assert relative_date_target("this month", today) == ("month", (2026, 8))
    assert relative_date_target("tomorrow", today) == ("day", date(2026, 8, 13))
    assert relative_date_target("in 3 days", today) == ("day", date(2026, 8, 15))
    assert relative_date_target("in 2 months", today) == ("month", (2026, 10))
    assert relative_date_target("next week", today) is None  # a week is not a day
    assert relative_date_target("book Confocal C2", today) is None


def test_next_month_moves_the_proposed_date_into_next_month():
    from datetime import date
    plan = {"tool": "request_booking", "arguments": {
        "instrument_id": "ins-confocal-c2",
        "starts_at": "2026-08-16T10:00:00Z", "ends_at": "2026-08-16T12:00:00Z"}}
    out = apply_relative_date(plan, "book it next month for 2 hours", date(2026, 8, 12))
    assert out["arguments"]["starts_at"] == "2026-09-16T10:00:00Z"
    assert out["arguments"]["ends_at"] == "2026-09-16T12:00:00Z", "duration preserved"


def test_a_proposal_already_in_the_requested_month_is_untouched():
    from datetime import date
    args = {"instrument_id": "ins-confocal-c2",
            "starts_at": "2026-09-04T10:00:00Z", "ends_at": "2026-09-04T12:00:00Z"}
    out = apply_relative_date({"tool": "request_booking", "arguments": dict(args)},
                              "book it next month", date(2026, 8, 12))
    assert out["arguments"]["starts_at"] == args["starts_at"]


@pytest.mark.tools
def test_the_confocal_after_a_confocal_question_resolves_to_that_confocal():
    """The workflow's headline: 'Is the confocal free?' then 'book it next month' proposed
    Spinning Disk. Now the kind carries from history, and an ambiguous kind asks."""
    from server.agent.action import _instrument_rows, _referenced_instrument
    rows = _instrument_rows()
    # history named a concrete confocal -> that one
    assert _referenced_instrument("book it", "assistant: Confocal C2 is free", rows) == (
        "ins-confocal-c2", [])
    # history said only "the confocal" -> ambiguous, ask (never Spinning Disk)
    choice, family = _referenced_instrument(
        "book it next month for 2 hours", "assistant: The confocal is free on 2027-04-15", rows)
    assert choice is None
    assert set(family) == {"ins-confocal-c2", "ins-confocal-c3"}


def test_an_injected_lab_label_is_caught():
    """bob relabelling his own rows as lab-a: the lab is neither his nor in the rows."""
    from server.auth import Ctx
    bob = Ctx(user_id="u-bob", name="Bob", role="user", lab_ids=("lab-b",),
              facility_ids=(), raw={})
    rows = [{"id": "bk-1", "account_code": "ACC-B1", "instrument": "MiSeq M3"}]
    assert _mentions_unsupported_lab("Lab-A has 17 bookings.", rows, {}, bob) is True
    # a lab present in the rows, or the caller's own, is fine
    assert _mentions_unsupported_lab("Your lab-b bookings: 17.", rows, {}, bob) is False
    asha_rows = [{"lab_id": "lab-a", "amount": 5514.50}]
    asha = Ctx(user_id="u-asha", name="Asha", role="pi", lab_ids=("lab-a",),
               facility_ids=(), raw={})
    assert _mentions_unsupported_lab("Lab A spent $5514.50.", asha_rows, {}, asha) is False


@pytest.mark.tools
def test_the_data_planner_is_given_the_callers_account_codes():
    """The billing planner grabbed the caller's lab id as an account_code because its
    own codes were not in the context — get_billing_summary('lab-a', ...) then refused a
    user's own-spend question. The codes must be available to plan with."""
    from server.agent.data import _caller_codes
    from server.auth import Ctx
    alice = Ctx(user_id="u-alice", name="Alice", role="user", lab_ids=("lab-a",),
                facility_ids=(), raw={})
    assert _caller_codes(alice) == ["ACC-A1"]


# --- defects found by the confirmation workflow, 2026-08-12 (round 2) ---------------

from server.agent.data import (  # noqa: E402
    _all_null,
    _assert_may_read_lab,
    _assert_may_read_named_person,
    _quantise,
)
from server.mcp.errors import ToolError  # noqa: E402


def _ctx(uid, name, role, labs):
    from server.auth import Ctx
    return Ctx(user_id=uid, name=name, role=role, lab_ids=tuple(labs), facility_ids=(), raw={})


@pytest.mark.parametrize(
    ("who", "question", "refused"),
    [
        (("u-alice", "Alice Nguyen", "user", ["lab-a"]), "What did Lab A spend in March 2026?", True),
        (("u-alice", "Alice Nguyen", "user", ["lab-a"]), "How much has my lab spent?", True),
        (("u-alice", "Alice Nguyen", "user", ["lab-a"]), "How much did I spend in March 2026?", False),
        (("u-alice", "Alice Nguyen", "user", ["lab-a"]), "What instruments are in Lab A?", False),
        (("u-asha", "Asha Patel", "pi", ["lab-a"]), "What did Lab A spend in March 2026?", False),
        (("u-asha", "Asha Patel", "pi", ["lab-a"]), "What did Lab B spend in March 2026?", True),
        (("u-cora", "Cora", "admin", ["lab-a", "lab-b"]), "What did Lab B spend?", False),
    ],
)
def test_a_lab_aggregate_the_caller_cannot_see_is_refused(who, question, refused):
    ctx = _ctx(*who)
    if refused:
        with pytest.raises(ToolError):
            _assert_may_read_lab(question, ctx)
    else:
        _assert_may_read_lab(question, ctx)  # must not raise


@pytest.mark.parametrize(
    ("who", "question", "refused"),
    [
        (("u-bob", "Bob Okafor", "user", ["lab-b"]), "What is on Alice Nguyen's invoice?", True),
        (("u-bob", "Bob Okafor", "user", ["lab-b"]), "What is on u-nobody's invoice?", True),
        (("u-bob", "Bob Okafor", "user", ["lab-b"]), "What is on Jordan Fakeperson's invoice?", True),
        (("u-bob", "Bob Okafor", "user", ["lab-b"]), "What is on my invoice?", False),
        (("u-alice", "Alice Nguyen", "user", ["lab-a"]), "What is on Alice's invoice?", False),
        (("u-alice", "Alice Nguyen", "user", ["lab-a"]), "How much did I spend?", False),
    ],
)
def test_a_user_reading_another_named_person_is_refused_identically(who, question, refused):
    """Real name and made-up name must refuse the same way — no existence oracle."""
    ctx = _ctx(*who)
    if refused:
        with pytest.raises(ToolError):
            _assert_may_read_named_person(question, ctx)
    else:
        _assert_may_read_named_person(question, ctx)


def test_numeric_artifacts_are_quantised_for_display():
    from decimal import Decimal
    assert _quantise(Decimal("225.5000000000000000")) == Decimal("225.50")
    assert _quantise(Decimal("0E-20")) == Decimal("0.00")
    assert _quantise(Decimal("412.00")) == Decimal("412.00")
    assert _quantise(20) == 20                # a count is left an int
    assert _quantise("in_prep") == "in_prep"
    assert _quantise(None) is None


def test_an_all_null_aggregate_row_is_recognised():
    assert _all_null([{"sum": None}]) is True
    assert _all_null([{"sum": None, "count": None}]) is True
    assert _all_null([{"sum": Decimal("5")}]) is False
    assert _all_null([{"a": None}, {"a": None}]) is False  # more than one row


def test_the_pending_booking_question_is_answered_from_state():
    from server.agent.knowledge import _pending_booking_answer
    hist = ("  assistant (approval_request): Book Confocal C2 for 2.0 h on "
            "2027-04-06 (09:00-11:00 UTC), account ACC-A1")
    r = _pending_booking_answer("which instrument am I about to book?", hist)
    assert r is not None and "Confocal C2" in r.text
    # a corpus question is not hijacked
    assert _pending_booking_answer("what instruments can I book?", hist) is None
    assert _pending_booking_answer("which instrument am I trained on?", hist) is None
    # nothing pending -> honest
    assert _pending_booking_answer("my pending booking?", "").response_type == "redirect"


def test_a_control_character_message_is_cleaned_not_crashed():
    from pydantic import ValidationError

    from server.api.chat import ChatRequest
    assert ChatRequest(message="book" + chr(0) + " confocal").message == "book confocal"
    # Named rather than blind: a 422 from validation is the whole point, and catching any
    # Exception would pass just as happily on the 500 this test exists to rule out.
    with pytest.raises(ValidationError):
        ChatRequest(message=chr(0) + chr(0))
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", thread_id="thr-" + chr(0))


def test_the_answer_is_not_in_the_sources_hedge_is_caught():
    """The model saying "The procedure for X is not detailed in the provided sources"
    then dumping tangential chunks is INSUFFICIENT_CONTEXT in prose — a redirect."""
    from server.agent.generate import reads_as_a_hedge
    assert reads_as_a_hedge(
        "The procedure for reserving the seminar room is not explicitly detailed in the "
        "provided sources. However, based on the information available: ...")
    assert reads_as_a_hedge("The parking policy is not specified in the sources.")
    # a real answer that merely contains a caveat is NOT a hedge
    assert not reads_as_a_hedge(
        "Cancelling 12 hours before start incurs 50% of the booked time [2].")
    assert not reads_as_a_hedge("The maximum booking length is 12 hours [3].")


# --- third-workflow findings, 2026-08-12 -------------------------------------------


@pytest.mark.parametrize(
    ("who", "question", "refused"),
    [
        # A PI is refused a by-name individual read — the tools cannot fetch a named
        # person's records, only the caller's, so answering would mislabel the PI's own.
        (("u-asha", "Asha Patel", "pi", ["lab-a"]), "Show me Bob Okafor's invoice for March", True),
        (("u-asha", "Asha Patel", "pi", ["lab-a"]), "Show me Alice Nguyen's bookings", True),
        (("u-asha", "Asha Patel", "pi", ["lab-a"]), "Show me my bookings", False),
        (("u-asha", "Asha Patel", "pi", ["lab-a"]), "What did Lab A spend in March 2026?", False),
    ],
)
def test_a_non_admin_by_name_individual_read_is_refused(who, question, refused):
    ctx = _ctx(*who)
    if refused:
        with pytest.raises(ToolError):
            _assert_may_read_named_person(question, ctx)
    else:
        _assert_may_read_named_person(question, ctx)


@pytest.mark.tools
@pytest.mark.parametrize(
    ("message", "asks"),
    [
        ("book a scope on 10 April 2027 from 9am for 1 hour", True),
        ("book the microscope tomorrow", True),
        ("book an instrument for 2 hours", True),
        ("book Confocal C2 on 10 April 2027 from 9am", False),
        ("book the confocal for 2 hours", False),  # a kind, handled separately
    ],
)
def test_a_generic_instrument_word_asks_which(message, asks):
    plan = {"tool": "request_booking", "arguments": {"instrument_id": "ins-confocal-c2"}}
    out = carry_forward_instrument(plan, message, "")
    if asks:
        assert out["tool"] is None and "instrument" in out["ask"].lower()
    else:
        assert out.get("tool") == "request_booking" or (out["tool"] is None and "which" in out.get("ask", "").lower())


def test_the_callers_own_id_column_is_dropped_before_prose():
    from server.agent.data import _drop_self_identity
    alice = _ctx("u-alice", "Alice Nguyen", "user", ["lab-a"])
    rows = [{"user_id": "u-alice", "instrument": "MiSeq M3", "hours": 2}]
    trimmed, cols = _drop_self_identity(rows, ["user_id", "instrument", "hours"], alice)
    assert "user_id" not in cols and all("user_id" not in r for r in trimmed)
    # a column that varies (a PI's rollup) is kept
    multi = [{"user_id": "u-alice", "h": 1}, {"user_id": "u-bob", "h": 2}]
    _, cols2 = _drop_self_identity(multi, ["user_id", "h"], alice)
    assert "user_id" in cols2


# --- the two documented edges, now fixed (2026-08-12) ------------------------------

from datetime import date  # noqa: E402


def test_a_single_named_date_narrows_an_over_ranged_availability_window():
    from server.agent.data import _narrow_availability_to_named_day
    a = {"instrument_id": "ins-confocal-c2", "date_from": "2027-04-01", "date_to": "2027-04-30"}
    _narrow_availability_to_named_day(a, "what free slots does Confocal C2 have on 1 April 2027?")
    assert a["date_from"] == "2027-04-01" and a["date_to"] == "2027-04-01"


def test_a_real_date_range_is_not_narrowed():
    from server.agent.data import _narrow_availability_to_named_day
    a = {"date_from": "2027-04-01", "date_to": "2027-04-05"}
    _narrow_availability_to_named_day(a, "free slots between 1 April and 5 April 2027")
    assert a["date_to"] == "2027-04-05", "a between-range must be left alone"


def test_a_single_day_time_window_is_not_narrowed():
    """The demo's '14:00-16:00 on one day' spans zero days — leave the specific window."""
    from server.agent.data import _narrow_availability_to_named_day
    a = {"date_from": "2027-12-02T14:00:00Z", "date_to": "2027-12-02T16:00:00Z"}
    _narrow_availability_to_named_day(
        a, "Is Confocal C2 free on Thursday 2027-12-02 between 14:00 and 16:00 UTC?")
    assert a["date_from"] == "2027-12-02T14:00:00Z"


def test_explicit_dates_finds_the_dates_a_question_names():
    from server.agent.data import _explicit_dates
    assert _explicit_dates("free slots on 1 April 2027") == {date(2027, 4, 1)}
    assert _explicit_dates("on 2027-04-01") == {date(2027, 4, 1)}
    assert _explicit_dates("next month") == set()


def test_an_average_question_reports_the_mean_not_the_sum():
    from decimal import Decimal

    from server.agent.data import (
        _correct_sum_reported_as_average,
        column_averages,
        column_totals,
        wants_average,
    )
    rows = [{"instrument": n, "total_cost": Decimal(v)} for n, v in [
        ("B4", "451.00"), ("C2", "412.00"), ("Titan", "2972.50"),
        ("LS7", "680.00"), ("Nano", "999.00")]]
    assert column_totals(rows)["total_cost"] == Decimal("5514.50")
    assert column_averages(rows)["total_cost"] == Decimal("1102.90")
    assert wants_average("the average cost per instrument")
    assert not wants_average("what did Lab A spend")
    q = "What was the average cost per instrument?"
    # the sum stated as the average is corrected...
    fixed = _correct_sum_reported_as_average("The average is $5514.50.", q, rows)
    assert fixed is not None and "1102.90" in fixed and "5514.50" in fixed
    # ...but a correct mean is left alone
    assert _correct_sum_reported_as_average("The average is $1102.90.", q, rows) is None


# --- found by driving the browser, 2026-08-13 --------------------------------------


def test_a_premise_the_corpus_never_heard_of_is_not_affirmed():
    """"What is the neutron-star collimator booking policy?" came back as a VERIFIED
    answer opening "The neutron-star collimator booking policy is governed by the same
    rules as other instruments" — every later sentence true and cited, the fabrication
    smuggled in as the question's premise and affirmed."""
    from server.agent.knowledge import unsupported_premise

    class Chunk:
        def __init__(self, text): self.text = text
    chunks = [Chunk(
        "Cancelling more than 24 hours before the start is free; within 24 hours you are "
        "charged 50% of the booked time. Confocal lasers warm up for 30 minutes before "
        "quantitative imaging."
    )]
    flagged = unsupported_premise(
        "What is the neutron-star collimator booking policy?",
        "The neutron-star collimator booking policy is governed by the same rules.",
        chunks)
    # The run extends over every adjacent word the passage does not contain, so the exact
    # span depends on the passage; what matters is that the invented thing is named.
    assert flagged is not None and "neutron-star collimator" in flagged
    # a real question whose words are all in the passage is untouched
    assert unsupported_premise(
        "What am I charged if I cancel a booking 12 hours before it starts?",
        "You are charged 50% of the booked time.", chunks) is None
    # one unusual word beside a known one is a scientist's vocabulary, not a fabrication
    assert unsupported_premise(
        "How long must the confocal lasers warm up before quantitative imaging?",
        "They warm up for 30 minutes before quantitative imaging.", chunks) is None
    # named but never repeated in the answer -> the answer did not affirm it
    assert unsupported_premise(
        "Does the neutron-star collimator exist?",
        "I have no record of that instrument.", chunks) is None


def test_columns_that_are_alternatives_are_never_summed():
    """Three instruments a scientist is choosing between are not components of a total.
    The reply said "the total hourly rate is $143.00" against rates 42/46/55."""
    from decimal import Decimal

    from server.agent.data import column_totals
    alternatives = [
        {"instrument": "Confocal C2", "hourly_rate": 42.0, "score": 10},
        {"instrument": "Confocal C3", "hourly_rate": 46.0, "score": 10},
    ]
    assert column_totals(alternatives) == {}
    components = [{"instrument": "A", "amount": Decimal("451.00")},
                  {"instrument": "B", "amount": Decimal("412.00")}]
    assert column_totals(components) == {"amount": Decimal("863.00")}


def test_both_planners_resolve_relative_dates_from_the_same_clock():
    """The read path pinned "today" to 2026-03-31 while the write path used the real
    clock, so a user was told a slot was free "tomorrow" and then handed a booking for a
    different date whose availability had never been checked."""
    from datetime import UTC, datetime

    from server.agent.data import PLANNER_SYSTEM
    assert "{today}" in PLANNER_SYSTEM, "the data planner must be told the real date"
    assert "2026-03-31" not in PLANNER_SYSTEM.split("{today}")[0][-400:], \
        "no pinned reference date may remain beside it"
    # and the action planner already resolves against the same clock
    from server.agent.action import relative_date_target
    today = datetime.now(UTC).date()
    kind, value = relative_date_target("book it tomorrow", today)
    assert kind == "day" and value.toordinal() == today.toordinal() + 1


# --- writes: the fields only the caller can settle ---------------------------------


@pytest.mark.tools
def test_the_callers_only_account_code_is_filled_in(ctxs):
    """"Book Confocal C2 from 3am to 5am" was refused for a missing account_code before
    its real problem — 3am is outside opening hours — was ever reached. Alice has exactly
    one code, so asking her for it asks for the only value she could have given."""
    from server.agent.action import _with_the_callers_only_account_code, account_codes_of

    assert account_codes_of(ctxs["alice"]) == ["ACC-A1"]
    plan = {"tool": "request_booking",
            "arguments": {"instrument_id": "ins-confocal-c2",
                          "starts_at": "2027-04-02T09:00:00Z",
                          "ends_at": "2027-04-02T11:00:00Z"}}
    out = _with_the_callers_only_account_code(plan, ctxs["alice"])
    assert out["arguments"]["account_code"] == "ACC-A1"


@pytest.mark.tools
def test_a_code_the_planner_already_chose_is_left_alone(ctxs):
    from server.agent.action import _with_the_callers_only_account_code

    plan = {"tool": "request_booking",
            "arguments": {"instrument_id": "ins-confocal-c2", "account_code": "ACC-A1"}}
    assert _with_the_callers_only_account_code(plan, ctxs["alice"]) is None


@pytest.mark.tools
def test_a_tool_that_takes_no_account_code_is_never_given_one(ctxs):
    from server.agent.action import _with_the_callers_only_account_code

    plan = {"tool": "cancel_booking", "arguments": {"booking_id": "bk-0071"}}
    assert _with_the_callers_only_account_code(plan, ctxs["alice"]) is None


@pytest.mark.tools
def test_with_more_than_one_account_the_choice_stays_the_callers(ctxs):
    """Filling one in would charge an account they did not pick. Asking is the answer."""
    from server.agent.action import _which_account_code, _with_the_callers_only_account_code

    codes = ["ACC-A1", "ACC-A2"]

    import server.agent.action as action_mod

    original = action_mod.account_codes_of
    action_mod.account_codes_of = lambda ctx: codes
    try:
        plan = {"tool": "request_booking", "arguments": {"instrument_id": "ins-confocal-c2"}}
        assert _with_the_callers_only_account_code(plan, ctxs["alice"]) is None
        ask = _which_account_code(plan, ctxs["alice"])
        assert ask.response_type == "clarify"
        assert "ACC-A1" in ask.text and "ACC-A2" in ask.text
    finally:
        action_mod.account_codes_of = original


# --- SQL the planner hybridised out of two real view names -------------------------


@pytest.mark.parametrize(
    ("sql", "repaired"),
    [
        # billing.v_charges' schema on v_billing_lines' name. No such relation exists,
        # and the LLM repair pass writes it again often enough to cost a demo scene.
        ("SELECT SUM(amount) FROM billing.v_billing_lines WHERE lab_id = 'lab-a'",
         "SELECT SUM(amount) FROM v_billing_lines WHERE lab_id = 'lab-a'"),
        ("SELECT * FROM reporting.v_bookings", "SELECT * FROM v_bookings"),
        # A real domain view keeps its schema: v_bookings exists in two spaces and they
        # are not the same view.
        ("SELECT * FROM scheduling.v_bookings", None),
        ("SELECT * FROM billing.v_charges", None),
        ("SELECT * FROM v_billing_lines", None),
        # Nothing to fall back to, so the guard's own rejection stands.
        ("SELECT * FROM nonsense.made_up", None),
    ],
)
def test_a_schema_the_allow_list_never_had_is_dropped(sql, repaired):
    from server.agent.data import _without_an_invented_schema

    assert _without_an_invented_schema(sql) == repaired


# --- a location nobody gave, and a rate read from the wrong place ------------------


@pytest.mark.parametrize(
    ("question", "arguments", "stripped"),
    [
        # The transcript: New York, which nobody mentioned. Three cores two kilometres
        # apart came back as 5567.34 km, 5569.23 km and 5569.43 km, "nearest first".
        ("Show me the closes facility nearby?",
         {"near_latitude": "40.7128", "near_longitude": "-74.0060"}, True),
        ("which core is closest?", {"near_latitude": 51.5, "near_longitude": -0.1}, True),
        # Coordinates the caller actually typed are theirs and stay.
        ("cores near 51.5243, -0.1339",
         {"near_latitude": "51.5243", "near_longitude": "-0.1339"}, False),
        # A boolean where a place name belongs: matches no campus, so a question with a
        # good answer came back "no matched instruments were found".
        ("where is the nearest core that can do cryo-EM?",
         {"technique": "cryo-em", "campus": "true"}, True),
        # A campus the caller DID name stays, even one we do not have — "nothing on West
        # Campus" is a true answer, and widening it would answer something else.
        ("cores on West Campus", {"campus": "West Campus"}, False),
        ("what instruments does the Advanced Imaging Core have?",
         {"technique": "imaging", "campus": "Advanced Imaging Core"}, False),
        # Nothing to strip.
        ("where is the nearest cryo-EM core", {"technique": "cryo-em"}, False),
    ],
)
def test_a_location_the_caller_never_gave_is_not_measured_from(
    question, arguments, stripped
):
    from server.agent.data import _plan_without_an_invented_location

    plan = {"mode": "tool", "tool": "find_facilities", "arguments": arguments}
    out = _plan_without_an_invented_location(plan, question)
    assert (out is not None) == stripped
    if stripped:
        # Whatever was invented is gone; whatever the caller did say survives.
        for argument in ("near_latitude", "near_longitude", "campus"):
            assert str(out["arguments"].get(argument, "")).lower() in question.lower() \
                or argument not in out["arguments"]
        assert out["arguments"].get("technique") == arguments.get("technique")


@pytest.mark.parametrize(
    ("question", "instrument"),
    [
        ("how much does this cost for MALDI-TOF R2 in Riverside Campus", "ins-maldi"),
        ("what is the hourly rate for Confocal C2?", "ins-confocal-c2"),
        ("what does the MiSeq M3 cost per hour", "ins-miseq"),
        # The caller's own money is their invoice, not a published rate.
        ("how much was I charged for Confocal C2 in March", None),
        ("what is on my invoice for MALDI-TOF R2", None),
        # No instrument named, so there is no rate to look up.
        ("how much does it cost", None),
        ("show my bookings", None),
    ],
)
@pytest.mark.tools
def test_a_published_rate_is_read_from_the_catalogue(question, instrument):
    """"How much does MALDI-TOF R2 cost" was planned as the caller's own usage records
    and answered by printing all seventeen rows of them."""
    from server.agent.data import _plan_for_an_instrument_rate

    plan = {"mode": "tool", "tool": "get_usage_records", "arguments": {"scope": "user"}}
    out = _plan_for_an_instrument_rate(plan, question)
    assert (out or {}).get("arguments", {}).get("facility_id") == instrument
    if instrument:
        assert out["tool"] == "get_facility_catalog"


@pytest.mark.tools
def test_a_catalogue_lookup_resolves_whatever_the_caller_called_the_place(ctxs):
    """"What is MALDI-TOF R2 used for?" arrived as facility_id="MALDI-TOF R2" and was
    refused as "No such facility" — an instrument name is a different kind of
    identifier, not a wrong one, and its core was one join away."""
    from server.mcp import tools as tools_mod

    for spelling in ("MALDI-TOF R2", "ins-maldi", "Mass Spectrometry Core"):
        result = tools_mod.get_facility_catalog(ctxs["alice"], facility_id=spelling)
        assert result["facilities"][0]["name"] == "Mass Spectrometry Core"
        assert "MALDI-TOF R2" in {i["name"] for i in result["instruments"]}

    with pytest.raises(Exception, match="No such facility"):
        tools_mod.get_facility_catalog(ctxs["alice"], facility_id="not-a-thing")


def test_several_cores_and_no_location_produces_no_ranking():
    """"I cannot say which is closest" and "the closest is Advanced Imaging Core" in the
    same breath, with all 12 instruments in the result attributed to that one core."""
    from server.agent.data import _directory_without_a_ranking

    rows = [{"name": "Advanced Imaging Core", "campus": "North Campus"},
            {"name": "Genomics Core", "campus": "North Campus"},
            {"name": "Mass Spectrometry Core", "campus": "Riverside Campus"}]
    text = _directory_without_a_ranking(rows)
    assert "Mass Spectrometry Core (Riverside Campus)" in text
    assert "cannot say which is closest" in text
    for word in ("closest is", "nearest is", "km"):
        assert word not in text

    # One match is not a ranking: "the nearest core that does cryo-EM" is simply that
    # core, so the composed answer stands and this returns nothing.
    assert _directory_without_a_ranking(rows[:1]) is None


# --- changing a booking the conversation is already about --------------------------


@pytest.mark.parametrize(
    ("message", "is_action"),
    [
        # The transcript: answered as a read, listing bookings and their statuses.
        ("can I cancel the booking", True),
        ("cancel my next booking", True),
        ("can I reschedule it", True),
        ("reschedule it to 9am", True),
        ("move it to Friday", True),
        ("cancel bk-0f45f1dd", True),
        ("shorten the booking to 2 hours", True),
        # The rules, not the record. These keep their citations.
        ("what does the cancellation policy say?", False),
        ("what am I charged if I cancel a booking 12 hours before", False),
        ("how do I cancel a booking?", False),
        ("what happens if I cancel it", False),
        ("is there a fee to cancel it", False),
        # Neither.
        ("show my bookings", False),
        ("what is my next booking?", False),
    ],
)
def test_asking_to_change_a_booking_is_an_action_not_a_lookup(message, is_action):
    from server.agent.router import (
        _ABOUT_THE_RULES_RE,
        _HYPOTHETICAL_RE,
        ASKS_TO_CHANGE_A_BOOKING_RE,
    )

    routed = bool(
        ASKS_TO_CHANGE_A_BOOKING_RE.search(message)
        and not _HYPOTHETICAL_RE.search(message)
        and not _ABOUT_THE_RULES_RE.search(message)
    )
    assert routed == is_action


@pytest.mark.tools
def test_the_booking_under_discussion_is_resolved_from_the_thread(ctxs):
    """"Can I reschedule to 08:00 to 12:00" planned no booking_id at all and was refused
    as "That lookup does not take an instrument"; a neighbouring run supplied one that
    did not exist and was refused as "No such booking"."""
    import server.agent.action as action_mod
    from server.agent.action import _with_the_booking_being_discussed

    # Stubbed rather than read from the seed: whether the demo happens to hold an open
    # booking depends on what the last demo run cancelled, and a test that quietly skips
    # itself is a test nobody notices has stopped covering anything.
    real, other = "bk-aaaa1111", "bk-bbbb2222"
    original = action_mod.open_bookings_of
    action_mod.open_bookings_of = lambda ctx: [
        {"id": real, "starts_at": datetime(2026, 8, 20, 9, 0), "status": "requested",
         "instrument": "Bioanalyzer B4"},
    ]
    try:
        # No id at all, resolved from the id named earlier in the thread.
        plan = {"tool": "reschedule_booking",
                "arguments": {"starts_at": "x", "ends_at": "y"}}
        out = _with_the_booking_being_discussed(
            plan, "can I reschedule it", f"assistant: your latest booking is {real}",
            ctxs["alice"],
        )
        assert out["arguments"]["booking_id"] == real

        # No id and none named, but they have exactly one open — that is the one.
        plan = {"tool": "cancel_booking", "arguments": {}}
        out = _with_the_booking_being_discussed(plan, "cancel it", "", ctxs["alice"])
        assert out["arguments"]["booking_id"] == real

        # An id already valid is left exactly as it is.
        plan = {"tool": "cancel_booking", "arguments": {"booking_id": real}}
        assert _with_the_booking_being_discussed(
            plan, "cancel it", "", ctxs["alice"]
        ) is None

        # An id named in the thread that is not theirs never wins.
        plan = {"tool": "cancel_booking", "arguments": {"booking_id": other}}
        out = _with_the_booking_being_discussed(
            plan, "cancel it", f"assistant: {other}", ctxs["alice"]
        )
        assert out["arguments"]["booking_id"] == real
    finally:
        action_mod.open_bookings_of = original


@pytest.mark.tools
def test_an_action_id_from_an_approval_card_is_not_a_booking_id(ctxs):
    """It was "mentioned in the thread", which a substring check accepts and the caller's
    own records do not."""
    from server.agent.action import _with_the_booking_being_discussed

    plan = {"tool": "cancel_booking", "arguments": {"booking_id": "act-fbb6d203d097"}}
    history = "assistant: action act-fbb6d203d097 · recorded in the audit log"
    out = _with_the_booking_being_discussed(plan, "cancel it", history, ctxs["alice"])
    assert out is None or out["arguments"]["booking_id"].startswith("bk-")


@pytest.mark.tools
def test_a_write_drops_arguments_its_tool_has_no_parameter_for():
    from server.agent.action import _without_arguments_the_tool_does_not_take

    plan = {"tool": "reschedule_booking",
            "arguments": {"booking_id": "bk-1", "starts_at": "a", "ends_at": "b",
                          "instrument_id": "ins-bioanalyzer"}}
    out = _without_arguments_the_tool_does_not_take(plan)
    assert "instrument_id" not in out["arguments"]
    assert out["arguments"]["booking_id"] == "bk-1"

    # Nothing surplus, nothing to do.
    assert _without_arguments_the_tool_does_not_take(
        {"tool": "cancel_booking", "arguments": {"booking_id": "bk-1"}}
    ) is None

    # Trimming that would leave a required argument missing is not a repair.
    assert _without_arguments_the_tool_does_not_take(
        {"tool": "reschedule_booking", "arguments": {"instrument_id": "ins-x"}}
    ) is None


@pytest.mark.tools
def test_a_question_about_instruments_in_general_reads_all_of_them():
    """"Which instruments are offline?" surveyed ONE instrument the caller never named
    and reported "No instruments are offline" — Q-TOF 6546 is offline. Another run
    contradicted itself in a sentence: "Cryo-EM Titan is offline. Status is available.\""""
    from server.agent.data import _plan_across_every_instrument

    health = {"mode": "tool", "tool": "get_instrument_health",
              "arguments": {"instrument_id": "ins-spinning-disk"}}
    out = _plan_across_every_instrument(health, "which instruments are offline?")
    assert out == {"mode": "tool", "tool": "get_facility_catalog", "arguments": {}}

    # An instrument the caller DID name is exactly what the health tool is for.
    assert _plan_across_every_instrument(
        health, "is the Spinning Disk SD1 working?"
    ) is None
    assert _plan_across_every_instrument(
        {"mode": "tool", "tool": "get_my_bookings", "arguments": {}}, "any offline?"
    ) is None


@pytest.mark.tools
def test_a_missing_record_is_not_called_an_access_problem(ctxs):
    """Barcodes cannot be enumerated by a non-admin, so a sample that is absent and one
    that is someone else's must answer alike — that stays. The WORDING accused a caller
    who mistyped "SMP-0001" (real ones are BC1000xx) of reaching for others' data."""
    from server.mcp import tools as tools_mod
    from server.mcp.errors import ToolError

    with pytest.raises(ToolError) as caught:
        tools_mod.track_sample(ctxs["alice"], barcode="SMP-0001")
    assert caught.value.code == "forbidden", "still indistinguishable from a denial"
    assert "no such" in caught.value.message.lower()
    assert "not one of yours" in caught.value.message.lower()

    # An admin is told plainly, as before.
    with pytest.raises(ToolError) as caught:
        tools_mod.track_sample(ctxs["cora"], barcode="SMP-0001")
    assert caught.value.code == "not_found"


# --- counting is arithmetic, and arithmetic is done here ---------------------------


def test_a_categorical_breakdown_is_counted_not_guessed():
    """Asked to show 24 bookings the model volunteered the split and got it wrong three
    ways — 12/1/11, then 22/0/2 twice — against a real 20/1/3. The total was right each
    time, because the total was computed for it."""
    from server.agent.data import column_value_counts

    rows = [{"id": f"bk-{i}", "status": s, "instrument": "MALDI-TOF R2",
             "starts_at": "2026-03-01T12:00:00"}
            for i, s in enumerate(["completed"] * 20 + ["requested"] + ["cancelled"] * 3)]
    counts = column_value_counts(rows)
    assert counts["status"] == {"cancelled": 3, "completed": 20, "requested": 1}
    assert counts["instrument"] == {"MALDI-TOF R2": 24}
    # An id is distinct per row; counting it says nothing. A timestamp is not a category.
    assert "id" not in counts and "starts_at" not in counts


def test_a_verified_count_is_a_number_the_reply_may_use():
    """Handed the split but not allowed to say it, every figure was rejected as
    unsupported and a composed answer collapsed into a raw table of 17 rows."""
    from server.agent.data import _allowed_numbers

    rows = [{"status": s} for s in ["completed"] * 5 + ["cancelled"] * 2]
    allowed = _allowed_numbers(rows, "show my bookings", {})
    assert Decimal(5) in allowed and Decimal(2) in allowed
    assert Decimal(7) in allowed, "the row count itself"


def test_counting_needs_more_than_one_row():
    from server.agent.data import column_value_counts

    assert column_value_counts([{"status": "completed"}]) == {}
    assert column_value_counts([]) == {}
