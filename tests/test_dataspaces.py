"""The Data & Tools console must be readable as evidence, not as a diagram.

Every panel of it makes a claim about the running system — this schema holds that many
rows, that role can read this view, these are all the tools there are — and a claim that
is drawn once and then diverges is worse than no claim at all, because it is still
convincing. So the tests here pin each panel to the thing it describes rather than to a
fixture: counts against a direct count(*), the tool list against the live registry with a
tool added underneath it, the grants against the role that cannot see itself in the
application's own session.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from server.agent import catalog
from server.db import session_scope
from server.mcp import sql_guard
from server.mcp import tools as tools_mod


@pytest.fixture(scope="module")
def client():
    from server.main import app

    with TestClient(app) as c:
        yield c


def _get(client, token, path):
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


def _admin(client, tokens, path) -> dict:
    r = _get(client, tokens["cora"], path)
    assert r.status_code == 200, r.text
    return r.json()


def _every_endpoint() -> list[str]:
    """Every route this router publishes, with real values in the placeholders.

    Read from the OpenAPI schema rather than listed here, so an endpoint added later is
    covered by the admin-only tests without anyone remembering to extend them — which is
    exactly how a console grows a hole an ordinary user can see through.
    """
    from server.main import app

    paths = [p for p in app.openapi()["paths"] if p.startswith("/dataspaces")]
    assert paths, "no /dataspaces routes are registered"
    return [p.replace("{schema}", "policy").replace("{relation}", "statements") for p in paths]


# --- admin only, and invisible to everyone else --------------------------------------


@pytest.mark.parametrize("who", ["alice", "bob", "asha"])
def test_a_non_admin_gets_404_on_every_endpoint(who, client, tokens):
    """404 rather than 403, on all of them. An admin surface that answers "forbidden"
    has confirmed it exists, which is the fact a caller who may not use it is being
    denied. The tokens are real non-admin tokens, because an unauthenticated request is
    refused earlier and would pass this test without proving anything."""
    for path in _every_endpoint():
        r = _get(client, tokens[who], path)
        assert r.status_code == 404, f"{path} answered {r.status_code} for {who}"
        assert r.json()["detail"]["code"] == "not_found"


def test_an_admin_reaches_every_endpoint(client, tokens):
    """The mirror image, so the test above cannot pass by the router being broken."""
    for path in _every_endpoint():
        assert _get(client, tokens["cora"], path).status_code == 200, path


def test_the_console_needs_a_caller(client):
    for path in _every_endpoint():
        assert client.get(path).status_code in (401, 403)


# --- the spaces ----------------------------------------------------------------------


def test_every_row_count_matches_a_direct_count(client, tokens):
    """The one property that makes the panel worth reading. Counted, never estimated:
    pg_class.reltuples reports -1 on a freshly seeded table and would put a negative row
    count on screen."""
    body = _admin(client, tokens, "/dataspaces")
    seen = 0
    with session_scope() as s:
        for space in body["spaces"]:
            for relation in space["relations"]:
                actual = s.execute(
                    text(f"SELECT count(*) FROM {space['schema']}.{relation['name']}")
                ).scalar_one()
                assert relation["rows"] == actual, f"{space['schema']}.{relation['name']}"
                seen += 1
    assert seen >= 38, "the spaces panel lost relations somewhere"


def test_the_relations_listed_are_the_relations_the_database_has(client, tokens):
    """Read from information_schema per request, so a view added by a migration appears
    without this console being edited."""
    body = _admin(client, tokens, "/dataspaces")
    listed = {(sp["schema"], r["name"]) for sp in body["spaces"] for r in sp["relations"]}
    schemas = [sp["schema"] for sp in body["spaces"]]
    with session_scope() as s:
        actual = {
            (row[0], row[1])
            for row in s.execute(
                text(
                    "SELECT table_schema, table_name FROM information_schema.tables "
                    "WHERE table_schema = ANY(:schemas)"
                ),
                {"schemas": schemas},
            )
        }
    assert listed == actual


def test_each_space_states_a_purpose_taken_from_the_schema_comment(client, tokens):
    """The purpose is COMMENT ON SCHEMA, not prose in a component. Prose in a component
    describes what someone believed on the day they wrote it; a comment travels with the
    migration that changes the schema."""
    body = _admin(client, tokens, "/dataspaces")
    with session_scope() as s:
        comments = dict(
            s.execute(
                text(
                    "SELECT nspname, obj_description(oid, 'pg_namespace') FROM pg_namespace "
                    "WHERE nspname = ANY(:schemas)"
                ),
                {"schemas": [sp["schema"] for sp in body["spaces"]]},
            ).all()
        )
    for space in body["spaces"]:
        assert space["purpose"] == comments[space["schema"]]
        assert space["purpose"], f"{space['schema']} has no COMMENT ON SCHEMA"


def test_the_agents_read_only_role_is_shown_on_the_domain_views(client, tokens):
    """Postgres shows a session only its own grants, so a console that asked once — as
    echomind_app — would report that the agent's role can read nothing at all. Each role
    is asked about itself. This is the assertion that fails if that is ever collapsed."""
    body = _admin(client, tokens, "/dataspaces")
    by_schema = {sp["schema"]: sp for sp in body["spaces"]}
    # Reported from each session's current_user, not declared, so a deployment with
    # different role names cannot end up with a panel naming roles it never spoke to.
    assert body["roles_queried"] == ["echomind_app", "echomind_readonly"]

    for schema in ("reference", "scheduling", "activity", "billing", "policy"):
        assert "echomind_readonly" in by_schema[schema]["readable_by"], schema
        for relation in by_schema[schema]["relations"]:
            roles = {g["role"]: g["privileges"] for g in relation["grants"]}
            assert roles.get("echomind_readonly") == ["SELECT"], relation["name"]


def test_the_vendors_tables_are_not_readable_by_the_agents_role(client, tokens):
    """The segregation the console exists to show: echomind_readonly holds SELECT on the
    domain views and nothing at all on the base tables underneath them."""
    body = _admin(client, tokens, "/dataspaces")
    infinity = next(sp for sp in body["spaces"] if sp["schema"] == "infinity")
    assert "echomind_readonly" not in infinity["readable_by"]


def test_a_column_level_grant_is_reported_with_its_columns(client, tokens):
    """information_schema.role_table_grants does not carry column grants, so a panel built
    from it alone reports SELECT and INSERT on infinity.bookings and silently omits the
    UPDATE the application actually holds on three of its columns."""
    body = _admin(client, tokens, "/dataspaces")
    infinity = next(sp for sp in body["spaces"] if sp["schema"] == "infinity")
    bookings = next(r for r in infinity["relations"] if r["name"] == "bookings")
    app_grant = next(g for g in bookings["grants"] if g["role"] == "echomind_app")
    update = next(c for c in app_grant["column_privileges"] if c["privilege"] == "UPDATE")
    assert update["columns"] == ["ends_at", "starts_at", "status"]


# --- the tools -----------------------------------------------------------------------


def test_the_tool_list_is_the_registry_not_a_copy_of_it(client, tokens):
    body = _admin(client, tokens, "/dataspaces/tools")
    assert {t["name"] for t in body["tools"]} == set(tools_mod.TOOLS)
    assert body["total"] == len(tools_mod.TOOLS)
    assert body["read"] + body["write"] == body["total"]


def test_a_tool_added_after_import_still_appears(client, tokens, monkeypatch):
    """The staleness test. `catalog.SOURCES` and `catalog.BY_NAME` are built at import
    time, so a console that read them would describe the tools that existed when the
    process started — and would keep looking authoritative while doing it. The endpoint
    reads the registry per request and derives what the catalog has not caught up with,
    which is what this proves."""
    spec = dataclasses.replace(
        tools_mod.TOOLS["get_facility_catalog"],
        number=99,
        name="list_training_courses",
        tier="T0",
        description="Every training course a facility runs.",
    )
    monkeypatch.setitem(tools_mod.TOOLS, "list_training_courses", spec)

    body = _admin(client, tokens, "/dataspaces/tools")
    added = next(t for t in body["tools"] if t["name"] == "list_training_courses")
    assert body["total"] == len(tools_mod.TOOLS)
    assert added["write"] is False
    assert added["purpose"] == "Every training course a facility runs."
    # Derived through the catalog's own helpers rather than a second copy of the rule:
    # subjects from the same keyword matcher the gate uses, minimum role from the tier.
    assert added["subjects"] == ["facilities", "training"]
    assert added["min_role"] == "user"
    # And it is honestly marked as something nobody has described yet.
    assert added["catalogued"] is False


def test_a_write_tool_added_after_import_still_appears(client, tokens, monkeypatch):
    """Write tools are absent from the catalog by design — it registers sources records
    can be read from. They still have a tier, a purpose and arguments, and the console
    must not lose them to that absence."""
    spec = dataclasses.replace(
        tools_mod.TOOLS["request_booking"],
        number=98,
        name="cancel_training",
        tier="T3",
        write=True,
        description="Withdraw a training sign-off.",
    )
    monkeypatch.setitem(tools_mod.TOOLS, "cancel_training", spec)

    body = _admin(client, tokens, "/dataspaces/tools")
    added = next(t for t in body["tools"] if t["name"] == "cancel_training")
    assert added["write"] is True
    assert added["route"] == "action"
    assert added["min_role"] == "admin"


def test_every_tool_says_what_it_reads_and_when_it_is_called(client, tokens):
    body = _admin(client, tokens, "/dataspaces/tools")
    for tool in body["tools"]:
        assert tool["purpose"]
        assert tool["min_role"] in catalog.ROLE_RANK
        assert tool["reads"], f"{tool['name']} does not say what it reads"
        assert tool["route"] in ("data", "action")
        words = tool["asked_as"]
        assert set(words["subject_words"]) == set(tool["subjects"])
        # Write tools carry subjects but nothing keyword-matches its way to one, so the
        # console has to say how each is actually reached rather than let a list of words
        # imply routing that does not happen.
        assert words["how"]
        assert ("action planner" in words["how"]) is tool["write"]


def test_the_words_that_reach_a_tool_are_the_gates_own(client, tokens):
    """"When is it called" has to be the data the gate actually matches on, or the console
    is describing routing that does not happen."""
    body = _admin(client, tokens, "/dataspaces/tools")
    bookings = next(t for t in body["tools"] if t["name"] == "get_my_bookings")
    assert bookings["asked_as"]["phrases"] == sorted(catalog.BY_NAME["get_my_bookings"].keywords)
    assert bookings["asked_as"]["subject_words"]["bookings"] == sorted(
        catalog.SUBJECT_KEYWORDS["bookings"]
    )


# --- the pipeline --------------------------------------------------------------------


def test_the_pipeline_names_the_stages_that_can_refuse(client, tokens):
    body = _admin(client, tokens, "/dataspaces/pipeline")
    keys = [s["key"] for s in body["stages"]]
    assert keys == ["route", "gate", "plan", "validate", "scope", "execute", "after", "answer"]
    assert set(body["refusal_points"]) == {"route", "gate", "validate", "after", "answer"}


def test_the_pipelines_facts_are_read_from_the_modules_that_enforce_them(client, tokens):
    """Prose about code is fine here; a number about the running system is not, unless it
    is the number actually applied to the next question asked."""
    stages = {s["key"]: s for s in _admin(client, tokens, "/dataspaces/pipeline")["stages"]}
    assert stages["validate"]["facts"]["max_rows"] == sql_guard.MAX_ROWS
    assert set(stages["validate"]["facts"]["allowed_relations"]) == (
        set(sql_guard.ALLOWED_VIEWS) | set(sql_guard.ALLOWED_QUALIFIED)
    )
    assert set(stages["scope"]["facts"]["lab_scoped_relations"]) == set(sql_guard.LAB_SCOPED_VIEWS)
    assert stages["gate"]["facts"]["min_words_to_judge"] == catalog.MIN_WORDS_TO_JUDGE
    for stage in ("gate", "after"):
        for refusal in stages[stage]["refuses"]:
            assert refusal["says"] == catalog.REASON_TEXT[refusal["reason"]]


def test_the_write_path_is_described_separately_from_the_answer_path(client, tokens):
    """A write is not a stage in the line — it is where the line stops and a person
    decides. Golden rule 4 read off the registry rather than asserted."""
    body = _admin(client, tokens, "/dataspaces/pipeline")
    assert set(body["write_path"]["tools"]) == {
        name for name, spec in tools_mod.TOOLS.items() if spec.write
    }


# --- content -------------------------------------------------------------------------


def test_the_row_cap_is_reported_on_every_page(client, tokens):
    """Capped, and saying so. A viewer that quietly returns the first fifty of five
    hundred has told the reader something false about the size of the table."""
    body = _admin(client, tokens, "/dataspaces/rows/activity/v_usage?limit=10")
    assert body["cap"] == sql_guard.MAX_ROWS
    assert body["limit"] == 10
    assert body["returned"] == 10
    assert body["total"] == 1500
    assert str(body["cap"]) in body["cap_note"]
    assert str(body["total"]) in body["cap_note"]


def test_a_page_beyond_the_cap_is_refused_rather_than_silently_shrunk(client, tokens):
    over = sql_guard.MAX_ROWS + 1
    r = _get(client, tokens["cora"], f"/dataspaces/rows/activity/v_usage?limit={over}")
    assert r.status_code == 422


def test_paging_reaches_every_row_without_repeating_one(client, tokens):
    """The reason the response names its ordering: a LIMIT with no ORDER BY pages over an
    order Postgres never promised, so page two can repeat a row from page one and drop
    another entirely."""
    whole_rows: list[dict] = []
    while True:
        chunk = _admin(
            client, tokens,
            f"/dataspaces/rows/reporting/v_billing_lines?limit=200&offset={len(whole_rows)}",
        )
        whole_rows += chunk["rows"]
        if len(chunk["rows"]) < 200:
            break
    whole = {"rows": whole_rows}

    seen: list[str] = []
    for offset in range(0, len(whole["rows"]), 50):
        page = _admin(
            client, tokens, f"/dataspaces/rows/reporting/v_billing_lines?limit=50&offset={offset}"
        )
        assert page["ordered_by"]
        seen += [json.dumps(row, sort_keys=True) for row in page["rows"]]

    # Compared as a multiset, not a set. Two invoice lines can be identical — the same
    # instrument, period and amount, billed twice — and asserting every row distinct was
    # asserting something about the fixture rather than about paging. What paging owes is
    # that walking it reaches each row exactly once, which is this.
    assert sorted(seen) == sorted(
        json.dumps(row, sort_keys=True) for row in whole["rows"]
    )


def test_money_and_timestamps_survive_the_json_round_trip(client, tokens):
    """Decimal and datetime are not JSON, and the obvious fixes both lose something: float
    rounds money, and str(datetime) is not a format anything parses. The tool layer
    already answered this once — money as a string that keeps every digit, timestamps as
    ISO — and the console reuses that answer rather than inventing a second one."""
    charges = _admin(client, tokens, "/dataspaces/rows/billing/v_charges?limit=5")
    json.dumps(charges)  # the whole response, not merely the cells
    with session_scope() as s:
        expected = s.execute(
            text(
                "SELECT line_id, qty, unit_price, amount FROM billing.v_charges "
                "ORDER BY line_id LIMIT 5"
            )
        ).mappings().all()
    for row, actual in zip(charges["rows"], expected, strict=True):
        assert isinstance(actual["amount"], Decimal)
        assert row["amount"] == str(actual["amount"])
        assert Decimal(row["amount"]) == actual["amount"]

    bookings = _admin(client, tokens, "/dataspaces/rows/scheduling/v_bookings?limit=5")
    json.dumps(bookings)
    with session_scope() as s:
        starts = s.execute(
            text("SELECT starts_at FROM scheduling.v_bookings ORDER BY booking_id LIMIT 5")
        ).scalars().all()
    for row, actual in zip(bookings["rows"], starts, strict=True):
        assert isinstance(actual, datetime)
        assert datetime.fromisoformat(row["starts_at"]) == actual


def test_a_policy_row_carries_its_thresholds_dates_and_clause(client, tokens):
    """policy.statements is the awkward one: numeric, date and timestamptz in the same
    row, plus the clause a decision has to be able to cite."""
    body = _admin(client, tokens, "/dataspaces/rows/policy/statements?limit=11")
    json.dumps(body)
    assert body["total"] == 11
    with_threshold = [r for r in body["rows"] if r["threshold_hours"] is not None]
    assert with_threshold
    for row in with_threshold:
        Decimal(row["threshold_hours"])
    assert any(r["source_clause"] for r in body["rows"])
    for row in body["rows"]:
        datetime.fromisoformat(row["effective_from"])


def test_a_binary_column_is_described_rather_than_decoded(client, tokens):
    """The checkpoint tables hold LangGraph state as bytea. Handed to the JSON encoder it
    raises UnicodeDecodeError on the first non-UTF-8 byte and takes the response with it,
    so the cell says how big the blob is and stops."""
    body = _admin(client, tokens, "/dataspaces/rows/echomind/checkpoint_writes?limit=3")
    json.dumps(body)
    assert body["rows"], "no checkpoint rows to check"
    for row in body["rows"]:
        assert row["blob"].endswith("bytes, binary>")


def test_a_relation_the_console_will_not_page_says_why(client, tokens):
    """The corpus holds notes private to individual users, and no admin reads those — the
    library refuses it too. The relation is still listed with its real row count, so the
    refusal is a stated position rather than a gap in the page."""
    body = _admin(client, tokens, "/dataspaces")
    echomind = next(sp for sp in body["spaces"] if sp["schema"] == "echomind")
    corpus = next(r for r in echomind["relations"] if r["name"] == "chunks")
    assert corpus["browsable"] is False
    assert corpus["not_browsable"]
    assert corpus["rows"] > 0

    r = _get(client, tokens["cora"], "/dataspaces/rows/echomind/chunks")
    assert r.status_code == 403
    assert r.json()["detail"]["message"] == corpus["not_browsable"]


@pytest.mark.parametrize(
    "path",
    [
        "/dataspaces/rows/pg_catalog/pg_class",
        "/dataspaces/rows/public/anything",
        "/dataspaces/rows/echomind/no_such_table",
        "/dataspaces/rows/infinity/users; DROP TABLE infinity.users",
        "/dataspaces/rows/infinity/users%20WHERE%201=1",
    ],
)
def test_a_relation_outside_the_spaces_is_not_reachable(path, client, tokens):
    """The schema and relation are caller-supplied and end up in SQL, so they are resolved
    against information_schema first and refused if they are not a relation in one of this
    application's own spaces."""
    assert _get(client, tokens["cora"], path).status_code == 404


def test_the_infinity_tables_are_still_readable_by_this_console(client, tokens):
    """The vendor's tables are not reachable by agent SQL, which is the point of the
    segregation — but an admin reading the console runs as the application role and can
    see what the application can see. Worth pinning, because it is the difference between
    the console being wrong and the console being restricted."""
    body = _admin(client, tokens, "/dataspaces/rows/infinity/instruments?limit=3")
    assert body["kind"] == "table"
    assert body["total"] == 19
    assert len(body["rows"]) == 3
