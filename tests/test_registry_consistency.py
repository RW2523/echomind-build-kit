"""The tool registry, the planner's menu and the catalogue must describe the same system.

Every significant defect in this system's tool path has been a divergence between three
hand-maintained lists rather than a fault in any of them:

* cancel_booking and reschedule_booking were registered, tiered, tested and callable, and
  absent from the planner's hand-written menu — so the planner could not propose them and
  "cancel booking bk-0133" produced a new booking instead;
* a view was added to the SQL allow-list and not to the catalogue, which made it
  unreachable, because the relevance gate only considers catalogued sources;
* a tool with no catalogue entry fell back to deriving keywords from its own name, so
  "get_booking_policy" contributed the bare word "policy" and matched a question about
  parking permits.

The retrieval path does not have this class of bug, because test_rag_isolation makes the
permission predicate impossible to duplicate. This file is the same idea for the tool
surface: not "remember to update the other list", but "the lists cannot disagree".
"""

from __future__ import annotations

import re

import pytest

from server.agent import action as action_branch
from server.agent import catalog
from server.agent.data import TOOL_MENU, VIEW_SCHEMA
from server.mcp import sql_guard
from server.mcp import tools as tools_mod


def _menu_names(menu: str) -> set[str]:
    """Tool names as a menu line declares them: `name(param, param)` at the margin."""
    return set(re.findall(r"^(\w+)\(", menu, flags=re.MULTILINE))


# --- the planner can propose exactly the write tools that exist -----------------------


def test_the_write_menu_names_every_registered_write_tool():
    """A write tool absent from the menu is unreachable, however well it works."""
    assert _menu_names(action_branch.WRITE_TOOLS) == set(tools_mod.WRITE_TOOLS)


def test_the_write_menu_invents_no_tool():
    """And a menu naming a tool that does not exist teaches the planner to call it."""
    assert _menu_names(action_branch.WRITE_TOOLS) <= set(tools_mod.TOOLS)


def test_each_write_tool_is_offered_with_its_real_parameters():
    """The menu used to be retyped, so a parameter could be renamed in one place only."""
    for line in action_branch.WRITE_TOOLS.splitlines():
        found = re.match(r"^(\w+)\((.*)\)$", line)
        if not found:
            continue
        name, params = found.group(1), found.group(2)
        listed = [p.strip() for p in params.split(",") if p.strip()]
        assert listed == list(tools_mod.TOOLS[name].params), name


def test_every_write_tool_carries_guidance_the_planner_can_act_on():
    """A bare name tells the planner nothing about when to choose it."""
    lines = action_branch.WRITE_TOOLS.splitlines()
    for i, line in enumerate(lines):
        if not re.match(r"^\w+\(", line):
            continue
        assert i + 1 < len(lines), f"{line} has no description"
        assert lines[i + 1].startswith("    "), f"{line} has no indented description"
        assert len(lines[i + 1].strip()) > 20, f"{line}'s description says nothing"


def test_a_note_cannot_describe_a_tool_that_does_not_exist():
    """A stale note is how guidance outlives the thing it was written for."""
    assert set(action_branch._PLANNER_NOTES) <= set(tools_mod.TOOLS)


# --- the read menu and the catalogue agree with the registry --------------------------


def test_the_read_menu_names_only_registered_tools():
    named = _menu_names(TOOL_MENU)
    assert named
    assert named <= set(tools_mod.TOOLS)


def test_every_read_tool_is_catalogued():
    """The relevance gate only considers catalogued sources, so an uncatalogued tool is
    one the gate can never legitimately route a question to."""
    missing = set(tools_mod.READ_TOOLS) - set(catalog.BY_NAME)
    assert not missing, f"read tools with no catalogue entry: {sorted(missing)}"


def test_every_allow_listed_relation_is_catalogued():
    allowed = set(sql_guard.ALLOWED_VIEWS) | set(sql_guard.ALLOWED_QUALIFIED)
    catalogued = {s.name for s in catalog.SOURCES if s.kind == "view"}
    assert catalogued == allowed


def test_the_planners_schema_is_the_catalogues_schema():
    """One rendering, so a column described in one place and shown in the other cannot
    drift apart."""
    assert catalog.view_schema_text() == VIEW_SCHEMA


def test_the_schema_names_only_relations_the_validator_allows():
    allowed = set(sql_guard.ALLOWED_VIEWS) | set(sql_guard.ALLOWED_QUALIFIED)
    shown = {line.split("(", 1)[0] for line in VIEW_SCHEMA.splitlines() if "(" in line}
    assert shown == allowed


# --- the registry itself stays coherent ------------------------------------------------


def test_tool_numbers_are_contiguous_and_unique():
    """The specs and the tier matrix refer to tools by number."""
    numbers = sorted(spec.number for spec in tools_mod.TOOLS.values())
    assert numbers == list(range(1, len(numbers) + 1))


def test_read_and_write_partition_the_registry():
    assert set(tools_mod.READ_TOOLS) | set(tools_mod.WRITE_TOOLS) == set(tools_mod.TOOLS)
    assert not set(tools_mod.READ_TOOLS) & set(tools_mod.WRITE_TOOLS)


def test_every_tool_declares_a_tier_and_a_description():
    for name, spec in tools_mod.TOOLS.items():
        assert spec.tier, f"{name} declares no tier"
        assert spec.description.strip(), f"{name} has no description"
        assert spec.handler is not None, f"{name} has no handler"


@pytest.mark.parametrize("name", sorted(tools_mod.WRITE_TOOLS))
def test_a_write_tool_returns_a_pending_action_rather_than_doing_the_thing(name):
    """Golden rule 4, read off the code: a write handler ends at create_pending, and the
    executors that actually touch the platform are reached only through approval."""
    from server.mcp import actions as actions_mod

    assert name in actions_mod.executors(), f"{name} has no executor to run after approval"


# --- retrieval is measured, not assumed ------------------------------------------------


@pytest.mark.rag_isolation
def test_every_labelled_fact_is_retrievable_above_the_confidence_floor():
    """The instrument the repo was missing, wired into the suite.

    Every other number here is end-to-end, so a retrieval fault only shows when it changes
    a final answer: a five-section policy document collapsed into one chunk, the correct
    cancellation answer was refused for want of context, and corpus precision read 0.933
    throughout — precision judges what was retrieved, never what was missed.

    A fact below the floor is one the answer layer never sees, so being retrieved is not
    enough; it has to survive the gate.
    """
    from scripts.retrieval_eval import FLOOR, run

    results = run(k=8, who="u-alice")
    assert results, "the labelled set is empty"

    missing = [r.question for r in results if not r.found]
    assert not missing, f"facts no retrieval reaches: {missing}"

    under = [(r.question, r.score) for r in results if not r.above_floor]
    assert not under, f"facts retrieved but below the {FLOOR} floor: {under}"
