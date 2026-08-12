"""The source catalog and the data-relevance gate (pytest -m tools).

Two things are under test here. That the catalog still describes the system it claims to
describe — a read tool or a reporting view that exists and is not in it is drift, and
drift is what makes a registry worth less than no registry. And that the gate refuses
only what it should: every check has a test for the refusal AND a test for the question
that must still get through, because a false refusal is the more expensive bug.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from server.agent import catalog
from server.agent.data import TOOL_MENU, VIEW_SCHEMA
from server.mcp import sql_guard
from server.mcp import tools as tools_mod

pytestmark = pytest.mark.tools


# --- the catalog describes what is actually there ------------------------------------


def test_every_read_tool_appears_in_the_catalog():
    assert set(tools_mod.READ_TOOLS) <= set(catalog.BY_NAME)


def test_every_catalogued_read_tool_answers_about_a_known_subject():
    """A source with no subject can never be reached by the gate, so it is not catalogued
    at all — it is merely present. A new read tool has to say what it answers about."""
    for name in tools_mod.READ_TOOLS:
        source = catalog.BY_NAME[name]
        assert source.subjects, f"{name} is in the catalog but says nothing about what it answers"
        assert set(source.subjects) <= set(catalog.SUBJECTS)


def test_the_catalog_describes_no_tool_that_has_gone_away():
    """Drift runs both ways: a described tool that no longer exists is a description
    nobody will ever check against the thing it claims to describe."""
    assert set(catalog._READ_TOOL_FACTS) <= set(tools_mod.READ_TOOLS)


def test_a_read_tool_nobody_described_still_gets_an_entry(monkeypatch):
    """A registry that takes the API down when someone adds a tool is a registry people
    delete. An undescribed tool is catalogued coarsely, from its own name and purpose,
    and the completeness test above is where that gets noticed."""
    spec = dataclasses.replace(
        tools_mod.TOOLS["get_facility_catalog"],
        name="list_training_courses",
        tier="T0",
        description="Every training course a facility runs.",
    )
    monkeypatch.setitem(tools_mod.TOOLS, "list_training_courses", spec)
    derived = catalog._tool_source("list_training_courses")
    assert derived.subjects == ("facilities", "training")
    assert derived.min_role == "user"
    assert derived.purpose == "Every training course a facility runs."


def test_no_write_tool_is_offered_as_a_source_of_records():
    """Reads answer questions; writes propose actions and are approved, never queried."""
    assert set(tools_mod.WRITE_TOOLS).isdisjoint(catalog.BY_NAME)


def test_every_tool_on_the_planner_menu_is_catalogued():
    """The menu the planner is shown and the registry the gate reads must name the same
    tools, or the gate can refuse a question the planner would have answered."""
    named = set(re.findall(r"^(\w+)\(", TOOL_MENU, flags=re.MULTILINE))
    assert named
    assert named <= set(catalog.BY_NAME)


def test_the_catalogued_views_are_exactly_the_allow_listed_ones():
    catalogued = {s.name for s in catalog.SOURCES if s.kind == "view"}
    assert catalogued == set(sql_guard.ALLOWED_VIEWS)
    assert set(catalog._VIEW_FACTS) <= set(sql_guard.ALLOWED_VIEWS)
    for source in catalog.SOURCES:
        if source.kind == "view":
            assert source.fields, f"{source.name} is allow-listed but its columns are undescribed"


def test_the_catalogued_columns_match_the_schema_the_planner_is_shown():
    assert catalog.view_schema_text() == VIEW_SCHEMA


def test_every_source_declares_a_role_a_scope_and_plain_english_fields():
    for source in catalog.SOURCES:
        assert source.min_role in catalog.ROLE_RANK
        assert source.scope in catalog.SCOPES
        assert source.purpose.strip()
        assert source.fields, f"{source.name} exposes nothing"
        for meaning in source.fields.values():
            assert meaning.strip()


def test_the_minimum_role_is_derived_from_the_tier_rather_than_retyped():
    """T0/T1 serve the caller themselves, T2 is the PI ladder, T3 is admin only."""
    assert catalog.BY_NAME["get_facility_catalog"].min_role == "user"
    assert catalog.BY_NAME["get_billing_summary"].min_role == "user"
    assert catalog.BY_NAME["get_project_overview"].min_role == "pi"
    assert catalog.BY_NAME["run_readonly_sql"].min_role == "pi"
    assert catalog._min_role("T3") == "admin"
    assert catalog._min_role("") == "admin"  # an unreadable tier closes a source


# --- PRE: a source covers it, and the caller may read it -----------------------------


@pytest.mark.parametrize(
    "question",
    [
        "What is the total on my ACC-A1 invoice for March 2026?",
        "Show me my bookings",
        "Show me my usage records for March 2026",
        "Where is sample BC100000?",
        "What account codes can I charge to?",
        "Am I trained on the confocal?",
        "Is Confocal C2 free on 2 April 2027?",
        "Which instrument had the most downtime in March 2026, and how many hours?",
        "What is the status of my service request?",
        "Which instruments are in the imaging core?",
        "How many hours did I book last month?",
    ],
)
def test_the_pre_check_passes_a_question_a_source_covers(ctxs, question):
    result = catalog.pre(question, ctxs["alice"])
    assert result.passed is True
    assert result.reason == "ok"
    assert result.considered


def test_the_pre_check_names_the_sources_it_considered(ctxs):
    result = catalog.pre("What is on my March invoice?", ctxs["alice"])
    assert "get_billing_summary" in result.considered
    assert "billing" in result.subjects


@pytest.mark.parametrize(
    "question",
    [
        "What is the parking permit policy for visiting researchers?",
        "Does the staff cafeteria stay shut at the weekend?",
        "Who won the chemistry prize last year?",
    ],
)
def test_the_pre_check_refuses_a_question_no_source_covers(ctxs, question):
    result = catalog.pre(question, ctxs["alice"])
    assert result.passed is False
    assert result.reason == "no_source_covers_it"
    assert result.considered == ()


def test_a_user_is_refused_a_scope_only_a_pi_can_read(ctxs):
    """Project records start at the PI tier, so for a plain user there is no source at
    all — refused here rather than after a planner call and a rejected query."""
    result = catalog.pre("Who is on the Cortical Cell Atlas project?", ctxs["alice"])
    assert result.passed is False
    assert result.reason == "not_entitled"
    assert result.considered == ("get_project_overview",)


def test_the_same_project_question_passes_for_the_pi_who_owns_it(ctxs):
    result = catalog.pre("Who is on the Cortical Cell Atlas project?", ctxs["asha"])
    assert result.passed is True


def test_another_labs_figures_are_refused(ctxs):
    result = catalog.pre("What did Lab B spend in March 2026?", ctxs["asha"])
    assert result.passed is False
    assert result.reason == "not_entitled"


def test_a_pi_may_still_ask_about_her_own_lab(ctxs):
    result = catalog.pre("What did Lab A spend in March 2026?", ctxs["asha"])
    assert result.passed is True


def test_an_admin_may_ask_about_any_lab(ctxs):
    assert catalog.pre("What did Lab B spend in March 2026?", ctxs["cora"]).passed is True


def test_asking_which_instruments_a_lab_uses_is_not_a_figures_question(ctxs):
    """The catalogue is public. Only a lab's numbers are scoped, and refusing the
    catalogue question too would be a refusal nobody asked for."""
    assert catalog.pre("Which instruments does Lab B have?", ctxs["alice"]).passed is True


# --- PRE: the cases where refusing would be a guess -----------------------------------


def test_a_follow_up_is_never_refused_for_lack_of_a_subject(ctxs):
    """"And in April?" carries no subject of its own; the turn before it does."""
    result = catalog.pre("And in April?", ctxs["alice"], history="USER: my invoice for March")
    assert result.passed is True


def test_a_bare_period_is_read_as_a_filter_not_as_a_question(ctxs):
    assert catalog.pre("2026-03", ctxs["alice"]).passed is True


def test_a_question_about_the_callers_own_records_always_has_a_source(ctxs):
    assert catalog.pre("Could you remind me what I have got coming up?",
                       ctxs["alice"]).passed is True


# --- POST: did the result answer anything --------------------------------------------


def test_the_post_check_passes_when_rows_came_back():
    assert catalog.post([{"instrument": "Confocal C2", "amount": 412.00}]).passed is True


def test_the_post_check_detects_an_empty_result():
    result = catalog.post([])
    assert result.passed is False
    assert result.reason == "no_records"


def test_the_post_check_detects_an_all_null_aggregate():
    """SUM over no permitted rows comes back as one row of NULLs. It is not a zero."""
    result = catalog.post([{"total": None, "hours": None}])
    assert result.passed is False
    assert result.reason == "no_values"


def test_a_result_that_is_facts_rather_than_rows_still_counts_as_an_answer():
    """An instrument under maintenance has no free windows and a perfectly good reason,
    and that reason is the answer."""
    result = catalog.post(
        [], {"bookable": False, "unavailable_reason": "Light Sheet LS7 is maintenance"}
    )
    assert result.passed is True


def test_a_zero_is_a_figure_and_is_not_treated_as_missing():
    assert catalog.post([{"total": 0}]).passed is True


# --- the refusals read as English -----------------------------------------------------


# Two or more lowercase words joined by underscores — a schema identifier, never English.
_IDENTIFIER_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


@pytest.mark.parametrize("reason", sorted(catalog.REASON_TEXT))
def test_every_refusal_is_plain_english_with_no_field_names(reason):
    text = catalog.redirect_text(catalog.RelevanceResult(False, reason))
    assert not _IDENTIFIER_RE.search(text.lower()), f"{reason} spells a field name"
    assert not [s.name for s in catalog.SOURCES if s.name in text]
    assert not re.search(r"\b(select|null|sum|row|column)s?\b", text, flags=re.IGNORECASE)
    assert text.endswith(".")
    assert len(text.split()) > 8  # a refusal explains itself; it does not just say no


def test_a_refusal_lists_what_the_assistant_can_look_up_instead():
    text = catalog.redirect_text(catalog.RelevanceResult(False, "no_source_covers_it"))
    for topic in ("bookings", "invoices", "training"):
        assert topic in text


def test_an_unknown_reason_still_produces_an_honest_sentence():
    text = catalog.redirect_text(catalog.RelevanceResult(False, "something_new"))
    assert text.strip()
    assert "_" not in text
