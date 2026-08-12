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
    _display,
    _mentions_unsupported_lab,
    _rows_from_tool_result,
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
    assert columns == ["starts_at", "ends_at"]


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
    from server.api.chat import ChatRequest
    assert ChatRequest(message="book" + chr(0) + " confocal").message == "book confocal"
    with pytest.raises(Exception):  # nothing legible left -> validation error, not a 500
        ChatRequest(message=chr(0) + chr(0))
    with pytest.raises(Exception):
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
        assert out.get("tool") == "request_booking" or out["tool"] is None and "which" in out.get("ask", "").lower()


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
