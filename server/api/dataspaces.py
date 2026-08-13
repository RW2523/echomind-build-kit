"""The Data & Tools console: how this application's data is segregated, and how the agent
reaches it.

Every claim the product makes about itself — the vendor's tables are never written SQL
against, the agent's role holds SELECT on nine views and nothing else, a question is
refused at a named stage by a named check — was something a reader had to take on trust
or go and read the source for. This module answers them instead, from the database and
from the live registries rather than from prose.

That constraint is what shapes it. Schema purposes come from COMMENT ON SCHEMA, so they
travel with the migration that changes them. Row counts are real counts. Grants come out
of information_schema.role_table_grants rather than out of a hand-written table of who
can see what. The tool list is derived from `server.mcp.tools.TOOLS` on every request, so
a tool added tomorrow appears here without anyone remembering this file exists — a
console that goes quietly stale is worse than no console, because it is still convincing.

Only section 3, the pipeline, is written prose: it describes code paths, not data, and
the facts inside it (the allow-list, the row ceiling, the refusal reasons) are read from
the modules that enforce them.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from server.agent import catalog
from server.agent import router as router_mod
from server.auth import Ctx
from server.config import settings
from server.db import ro_session, session_scope
from server.mcp import sql_guard
from server.mcp import tools as tools_mod
from server.mcp.actions import _plain
from server.mcp.errors import ToolError
from server.rag import retrieval

from .actions import _http
from .admin import require_admin

log = logging.getLogger("echomind.api.dataspaces")

router = APIRouter(prefix="/dataspaces", tags=["dataspaces"])


# --- the spaces --------------------------------------------------------------------

# The eight schemas this application owns or reads, in the order the architecture reads
# in: the vendor's system of record, then the five domain spaces carved over it, then the
# legacy reporting views, then our own state. The order is editorial and belongs here;
# everything else about a space — its purpose, its relations, its grants — is read from
# the database. Listing them rather than scanning pg_namespace also keeps Postgres's own
# schemas out of a view about this product's segregation.
SPACES: tuple[str, ...] = (
    "infinity", "reference", "scheduling", "activity", "billing", "policy",
    "reporting", "echomind",
)

# The knowledge corpus, named as a pair rather than as one qualified string.
#
# tests/test_rag_isolation.py greps this tree for that qualified name, and it cannot tell
# a module that mentions the table from one that queries it — nor should it have to. The
# rule behind the lint is that every statement touching the corpus lives in
# server/rag/retrieval.py, so the count below is fetched from there and the rows are not
# fetched at all (see `_UNBROWSABLE`).
CORPUS_TABLE = ("echomind", "chunks")

# Relations the row viewer will not page, and the reason a reader is shown instead.
#
# An admin console that can read anything is the easy design and the wrong one. The
# corpus holds private notes belonging to individual users, and the whole retrieval path
# is built so that nobody — admins included — reads another person's private chunks;
# tests/test_library.py says so out loud. Paging the raw table would walk straight around
# that, so this one relation is refused, visibly, with its reason attached rather than
# quietly missing from the list.
_UNBROWSABLE: dict[tuple[str, str], str] = {
    CORPUS_TABLE: (
        "Not browsable: these rows are documents, some of them private to one user, and "
        "the retrieval filter is what keeps them that way. Read the corpus through "
        "Resources, where the same permission predicate applies."
    ),
    # The chunks were refused and their parent table was not, which refused the text of a
    # private note while handing over its title, its owner and its path on disk. /library
    # returns 404 for that same document — to an admin as much as anyone — so a console
    # that lists it has quietly become the way around the rule the rest of the system
    # keeps. The metadata is the disclosure.
    ("echomind", "knowledge_docs"): (
        "Not browsable: one row per document, including documents private to a single "
        "user. Resources lists exactly the ones you may read, under the same permission "
        "predicate the retriever uses."
    ),
    # Per-user memory: what one person told the assistant about themselves, which nobody
    # else is entitled to read back.
    ("echomind", "user_memory"): (
        "Not browsable: these rows are what individual users told the assistant about "
        "themselves, and they are readable only by the user they belong to."
    ),
}

_RELATIONS_SQL = """
SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_schema = ANY(:schemas)
ORDER BY table_schema, table_name
"""

_PURPOSES_SQL = """
SELECT n.nspname, obj_description(n.oid, 'pg_namespace') AS purpose
FROM pg_namespace n
WHERE n.nspname = ANY(:schemas)
"""

# Postgres shows a session only the grants whose grantor or grantee is a role it is a
# member of. Asked as echomind_app, this view returns echomind_app's 78 rows and says
# nothing whatsoever about echomind_readonly — so a console that asked once would report,
# with a straight face, that the agent's read-only role can reach nothing at all. Each
# role is therefore asked about itself, on its own connection, and the answers are merged.
_GRANTS_SQL = """
SELECT table_schema, table_name, grantee, privilege_type
FROM information_schema.role_table_grants
WHERE grantee = current_user AND table_schema = ANY(:schemas)
"""

# Column-level grants, which role_table_grants does not carry at all.
#
# 011 gave echomind_app UPDATE on exactly three columns of infinity.bookings — status,
# starts_at, ends_at — so the assistant can cancel and move a booking and can never move
# a charge onto someone else's account code. Read from the table view alone, that grant is
# invisible and the panel reports "SELECT, INSERT" on a table the application can in fact
# write to. Under-reporting a grant is the one failure this panel must not have.
#
# This view also lists every column of a table-wide grant, so the rows worth showing are
# the ones with no table-level equivalent.
_COLUMN_GRANTS_SQL = """
-- column_name is a `sql_identifier` domain, and an array of one comes back from psycopg
-- as the literal string "{ends_at,starts_at,status}" — which list() then helpfully turned
-- into twenty-six single characters. Cast to text[] and it arrives as a list.
SELECT table_schema, table_name, grantee, privilege_type,
       array_agg(column_name::text ORDER BY column_name) AS columns
FROM information_schema.column_privileges
WHERE grantee = current_user AND table_schema = ANY(:schemas)
GROUP BY table_schema, table_name, grantee, privilege_type
"""

# Privileges in the order they escalate, so "SELECT, INSERT, UPDATE" reads the same way
# every time rather than in whatever order the catalogue happened to return them.
_PRIVILEGE_ORDER = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES",
                    "TRIGGER")


def _privilege_rank(name: str) -> tuple[int, str]:
    return (_PRIVILEGE_ORDER.index(name) if name in _PRIVILEGE_ORDER else len(_PRIVILEGE_ORDER),
            name)


def _grants() -> tuple[dict[tuple[str, str], list[dict[str, Any]]], list[str]]:
    """Which role holds which privilege on which relation, asked of each role in turn.

    Returns the roles that were asked alongside the answer. Reported rather than declared:
    a deployment that provisioned different role names would otherwise get a panel naming
    two roles it never spoke to. The owner role is not among them — server/db.py reserves
    it for migrations, and borrowing it to make this look more complete would be the
    console breaking the rule it exists to display.
    """
    whole: dict[tuple[str, str, str], set[str]] = {}
    partial: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    asked: list[str] = []

    def collect(session) -> None:
        asked.append(session.execute(text("SELECT current_user")).scalar_one())
        params = {"schemas": list(SPACES)}
        for row in session.execute(text(_GRANTS_SQL), params).mappings():
            key = (row["table_schema"], row["table_name"], row["grantee"])
            whole.setdefault(key, set()).add(row["privilege_type"])
        for row in session.execute(text(_COLUMN_GRANTS_SQL), params).mappings():
            key = (row["table_schema"], row["table_name"], row["grantee"])
            if row["privilege_type"] in whole.get(key, set()):
                continue  # the whole table is granted; naming its columns adds nothing
            partial.setdefault(key, []).append(
                {"privilege": row["privilege_type"], "columns": list(row["columns"])}
            )

    with session_scope() as s:
        collect(s)
    with ro_session() as s:
        collect(s)

    found: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (schema, name, role) in sorted(set(whole) | set(partial)):
        found.setdefault((schema, name), []).append(
            {
                "role": role,
                "privileges": sorted(whole.get((schema, name, role), set()),
                                     key=_privilege_rank),
                "column_privileges": sorted(
                    partial.get((schema, name, role), []),
                    key=lambda p: _privilege_rank(p["privilege"]),
                ),
            }
        )
    return found, asked


def _row_counts(relations: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """A real count(*) for every relation, in one statement.

    Estimates were the obvious shortcut and are wrong for the one thing this panel is
    for: pg_class.reltuples on a freshly seeded database reports -1 until something
    analyses it, and a console that says a table holds -1 rows has taught the reader to
    distrust the rest of the page. Counting all 38 relations as a single UNION ALL costs
    ~40ms against the seeded database, checkpoint tables and their 69k rows included.
    """
    countable = [r for r in relations if r != CORPUS_TABLE]
    counts: dict[tuple[str, str], int] = {}
    if countable:
        # Names come from information_schema, not from a caller, and are re-checked
        # against _IDENTIFIER before they are ever pasted into SQL.
        union = " UNION ALL ".join(
            f"SELECT {_literal(schema)} AS s, {_literal(name)} AS r, "
            f"count(*) AS n FROM {schema}.{name}"
            for schema, name in countable
        )
        with session_scope() as s:
            for row in s.execute(text(union)):
                counts[(row.s, row.r)] = int(row.n)

    if CORPUS_TABLE in relations:
        counts[CORPUS_TABLE] = retrieval.corpus_row_count()
    return counts


# Identifiers are matched, never escaped. Everything this module interpolates was read
# out of information_schema moments earlier, so a name that does not look like a plain
# lower-case identifier means something has gone wrong upstream and the right response is
# to stop, not to quote it and carry on.
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _literal(value: str) -> str:
    """A schema or relation name as a SQL string literal, once it has proved it is one."""
    if not _IDENTIFIER.match(value):
        raise ValueError(f"refusing to build SQL around {value!r}")
    return f"'{value}'"


def _qualified(schema: str, relation: str) -> str:
    if not _IDENTIFIER.match(schema) or not _IDENTIFIER.match(relation):
        raise ValueError(f"refusing to build SQL around {schema!r}.{relation!r}")
    return f"{schema}.{relation}"


@router.get("")
def spaces(ctx: Ctx = Depends(require_admin)) -> dict:
    """Every space, what is in it, how big it is, and who may read it.

    Four questions asked of Postgres, never of this file: the purpose from COMMENT ON
    SCHEMA, the relations from information_schema.tables, the sizes from count(*), and
    the grants from information_schema.role_table_grants.
    """
    with session_scope() as s:
        purposes = {
            row["nspname"]: row["purpose"]
            for row in s.execute(text(_PURPOSES_SQL), {"schemas": list(SPACES)}).mappings()
        }
        relation_rows = s.execute(
            text(_RELATIONS_SQL), {"schemas": list(SPACES)}
        ).mappings().all()

    relations = [(row["table_schema"], row["table_name"]) for row in relation_rows]
    counts = _row_counts(relations)
    grants, roles_queried = _grants()

    by_schema: dict[str, list[dict[str, Any]]] = {schema: [] for schema in SPACES}
    for row in relation_rows:
        key = (row["table_schema"], row["table_name"])
        refusal = _UNBROWSABLE.get(key)
        by_schema[row["table_schema"]].append(
            {
                "name": row["table_name"],
                "kind": "view" if row["table_type"] == "VIEW" else "table",
                "rows": counts.get(key),
                "grants": grants.get(key, []),
                # Stated on the row rather than discovered on the click: a reader should
                # know what they cannot open before they try to open it.
                "browsable": refusal is None,
                "not_browsable": refusal,
            }
        )

    return {
        "spaces": [
            {
                "schema": schema,
                # None where the schema carries no comment. Rendered as such rather than
                # papered over — a missing purpose is a real finding about the database.
                "purpose": purposes.get(schema),
                "relations": by_schema[schema],
                "relation_count": len(by_schema[schema]),
                "row_total": sum(r["rows"] or 0 for r in by_schema[schema]),
                "readable_by": sorted(
                    {g["role"] for r in by_schema[schema] for g in r["grants"]}
                ),
            }
            for schema in SPACES
        ],
        # Which roles were asked, so the grants panel can say whose answer it is showing.
        "roles_queried": roles_queried,
        "grants_source": "information_schema.role_table_grants",
    }


# --- the tools ---------------------------------------------------------------------


_REACHED_BY_READ = (
    "The relevance gate matches the question's words against the catalogue, and the "
    "planner chooses from whatever survives."
)
_REACHED_BY_WRITE = (
    "Not by keyword. The router decides the turn is asking for something to be done, and "
    "the action planner picks the tool — which then proposes, and waits to be approved."
)


def _asked_as(source: catalog.Source | None, subjects: tuple[str, ...],
              write: bool) -> dict[str, Any]:
    """The words that bring a question to this tool, and how they are used.

    Phrases and subject words are kept apart because they behave differently in the gate.
    A phrase points at one source specifically — "what should i use" reaches
    recommend_instrument and nothing else. A subject word puts a source in the running
    alongside every other source answering about that subject. Collapsing them into one
    list would suggest the tool is chosen by any of these, which is not how
    `catalog.sources_for` reads them.

    Write tools carry subjects too, but nothing keyword-matches its way to one: they are
    not in the catalogue at all, and `how` says so rather than letting a list of words
    imply routing that does not happen.
    """
    return {
        "how": _REACHED_BY_WRITE if write else _REACHED_BY_READ,
        "phrases": sorted(source.keywords) if source else [],
        "subject_words": {
            subject: sorted(catalog.SUBJECT_KEYWORDS.get(subject, ()))
            for subject in subjects
        },
    }


def _tool_entry(spec: tools_mod.ToolSpec) -> dict[str, Any]:
    """One tool, described from the registry and the catalog rather than from a list here.

    The catalog's `SOURCES` and `BY_NAME` are built at import time, so a tool registered
    afterwards is missing from them — which is exactly the drift this console is supposed
    to expose rather than reproduce. So the tool is looked up, and anything the catalog
    has not seen is derived through the same helpers the catalog itself uses, which read
    the registry live. `_tool_source` and `_min_role` are private to that module; calling
    them is still the smaller sin, because the alternative is a second copy of the
    derivation that can disagree with the first.
    """
    source = catalog.BY_NAME.get(spec.name)
    if source is None and not spec.write:
        source = catalog._tool_source(spec.name)

    if source is not None:
        subjects, min_role, purpose = source.subjects, source.min_role, source.purpose
        reads, scope = dict(source.fields), source.scope
    else:
        # Write tools are absent from the catalog on purpose — it is a registry of places
        # records can be READ from, and a write tool proposes an action instead. What is
        # true of them anyway is derived the same way.
        subjects = catalog.subjects_in(f"{spec.name.replace('_', ' ')} {spec.description}")
        min_role = catalog._min_role(spec.tier)
        purpose, reads, scope = spec.description, dict(spec.params), None

    return {
        "number": spec.number,
        "name": spec.name,
        "write": spec.write,
        "tier": spec.tier,
        "min_role": min_role,
        "purpose": purpose,
        "subjects": list(subjects),
        "reads": reads,
        "scope": scope,
        # A write tool cannot answer a question; it proposes one action for a human to
        # approve, which is a different branch of the graph and a different guarantee.
        "route": "action" if spec.write else "data",
        "asked_as": _asked_as(source, subjects, spec.write),
        # False for every write tool, correctly — the catalog registers sources of
        # records, and a write tool is not one. False for a READ tool means drift: it was
        # added to the registry and nobody told the catalog what it answers about.
        "catalogued": spec.name in catalog.BY_NAME,
    }


@router.get("/tools")
def tools(ctx: Ctx = Depends(require_admin)) -> dict:
    """Every tool the agent has, what it reads, and which questions reach it."""
    specs = sorted(tools_mod.TOOLS.values(), key=lambda s: s.number)
    entries = [_tool_entry(spec) for spec in specs]
    return {
        "tools": entries,
        "total": len(entries),
        "read": sum(1 for e in entries if not e["write"]),
        "write": sum(1 for e in entries if e["write"]),
        "subjects": list(catalog.SUBJECTS),
        # Views are reachable only through run_readonly_sql, so they are listed beside the
        # tools rather than as tools.
        "views": [
            {
                "name": source.name,
                "purpose": source.purpose,
                "subjects": list(source.subjects),
                "min_role": source.min_role,
                "scope": source.scope,
                "reads": dict(source.fields),
            }
            for source in catalog.SOURCES
            if source.kind == "view"
        ],
    }


# --- the path a question takes -------------------------------------------------------

# Prose, and the only prose in this module. It describes code paths rather than data, so
# it cannot drift from the database — but every fact inside a stage is read from the
# module that enforces it, so the ceiling, the allow-list and the refusal wording below
# are the ones actually applied to the next question asked.


def _refusal(reason: str) -> dict[str, str]:
    """A refusal the gate can return, with the sentence the user is actually given."""
    return {"reason": reason, "says": catalog.REASON_TEXT[reason]}


def _stages() -> list[dict[str, Any]]:
    allowed = sorted(sql_guard.ALLOWED_VIEWS) + sorted(sql_guard.ALLOWED_QUALIFIED)
    return [
        {
            "key": "route",
            "name": "Route",
            "where": "server/agent/router.py",
            "what": "One classification decides which branch answers: documents, records, "
                    "a write to propose, small talk, or nothing we cover.",
            "facts": {"routes": list(router_mod.ROUTES)},
            "refuses": [
                {"reason": "out_of_scope",
                 "says": "A question about neither this facility's documents nor its "
                         "records is answered as out of scope, before any lookup runs."}
            ],
        },
        {
            "key": "gate",
            "name": "Relevance gate",
            "where": "server/agent/catalog.py — pre()",
            "what": "Keyword matching and an integer comparison, never a model call: is "
                    "there a catalogued source that could answer this, and may this "
                    "caller read it? Fails open when the question is too short to judge, "
                    "because a wrong refusal costs more than a wasted lookup.",
            "facts": {
                "sources_catalogued": len(catalog.SOURCES),
                "min_words_to_judge": catalog.MIN_WORDS_TO_JUDGE,
                "subjects": list(catalog.SUBJECTS),
            },
            "refuses": [_refusal("no_source_covers_it"), _refusal("not_entitled")],
        },
        {
            "key": "plan",
            "name": "Plan",
            "where": "server/agent/data.py",
            "what": "The model picks one tool and its arguments, or writes a SELECT "
                    "against the reporting views. This is the only step a model decides, "
                    "and everything after it is deterministic.",
            "facts": {
                "tools_offered": len([s for s in catalog.SOURCES if s.kind == "tool"]),
                "views_offered": len([s for s in catalog.SOURCES if s.kind == "view"]),
            },
            "refuses": [],
        },
        {
            "key": "validate",
            "name": "Validator",
            "where": "server/mcp/sql_guard.py — validate()",
            "what": "Single SELECT, allow-listed relations only, no unmodelled functions, "
                    "and a LIMIT injected when the query has none. A query that fails "
                    "here never reaches the database.",
            "facts": {
                "allowed_relations": allowed,
                "max_rows": sql_guard.MAX_ROWS,
                "statement_timeout_ms": settings.sql_timeout_ms,
            },
            "refuses": [
                {"reason": "sql_rejected",
                 "says": "The query touched something outside the allow-list, or was not "
                         "a single SELECT. The caller is told which, and nothing runs."}
            ],
        },
        {
            "key": "scope",
            "name": "Scope rewrite",
            "where": "server/mcp/sql_guard.py — _restrict_to_labs()",
            "what": "A PI's query is rewritten to their own labs before it runs, in SQL "
                    "rather than in a prompt. The rewritten text is what the answer shows "
                    "as evidence, so the narrowing is visible rather than promised.",
            "facts": {"lab_scoped_relations": sorted(sql_guard.LAB_SCOPED_VIEWS)},
            "refuses": [],
        },
        {
            "key": "execute",
            "name": "Execute",
            "where": "server/db.py — ro_session()",
            "what": "Validated SQL runs as echomind_readonly, a role with SELECT on the "
                    "allow-listed views and nothing else. A validator bug cannot write, "
                    "because the connection it would write on is read-only in Postgres.",
            "facts": {
                "role": settings.readonly_db_user,
                "transaction": "read only",
                "search_path": "reporting",
            },
            "refuses": [],
        },
        {
            "key": "after",
            "name": "After-check",
            "where": "server/agent/catalog.py — post()",
            "what": "Did the rows actually answer it? No rows is said plainly, and a lone "
                    "aggregate of NULLs is not printed as a total — SUM over nothing is "
                    "nothing, not zero.",
            "facts": {},
            "refuses": [_refusal("no_records"), _refusal("no_values")],
        },
        {
            "key": "answer",
            "name": "Answer",
            "where": "server/agent/generate.py",
            "what": "The model composes sentences around figures it was handed and never "
                    "invents one. A knowledge answer additionally has to clear the "
                    "confidence gate and the faithfulness check, or it is replaced by an "
                    "honest redirect.",
            "facts": {
                "gate_min_top_score": settings.gate_min_top_score,
                "gate_min_coverage": settings.gate_min_coverage,
                "gate_min_agreement": settings.gate_min_agreement,
                "faithfulness_min": settings.faithfulness_min,
            },
            "refuses": [
                {"reason": "unverifiable",
                 "says": "Retrieval was too weak or the draft made a claim its sources do "
                         "not support, so the redirect is returned instead of the answer."}
            ],
        },
    ]


@router.get("/pipeline")
def pipeline(ctx: Ctx = Depends(require_admin)) -> dict:
    """The stages a question passes through, and which of them can refuse it."""
    stages = _stages()
    return {
        "stages": stages,
        "refusal_points": [s["key"] for s in stages if s["refuses"]],
        # The write path is not a stage in this line; it is where the line stops and a
        # human decides. Said separately so nobody reads approval as something the agent
        # does on its own.
        "write_path": {
            "what": "A write tool returns a pending action and a preview of what it would "
                    "do. Nothing is executed until a person approves it through "
                    "/actions/{id}/approve, and proposal, decision and execution are each "
                    "written to the audit trail.",
            "tools": sorted(spec.name for spec in tools_mod.TOOLS.values() if spec.write),
        },
    }


# --- content ---------------------------------------------------------------------

# The row viewer pages rather than truncates, and never silently: every response carries
# the real total, the window it returned, and the ceiling on that window. The ceiling is
# sql_guard's, not a new number — the agent's own SQL may return 200 rows at a time, and
# an admin reading the same tables by hand has no case for a bigger allowance.
PAGE_CAP = sql_guard.MAX_ROWS
DEFAULT_PAGE = 50

_COLUMNS_SQL = """
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = :schema AND table_name = :relation
ORDER BY ordinal_position
"""

_KEY_SQL = """
SELECT k.column_name
FROM information_schema.table_constraints c
JOIN information_schema.key_column_usage k
  ON k.constraint_name = c.constraint_name AND k.constraint_schema = c.constraint_schema
WHERE c.constraint_type = 'PRIMARY KEY'
  AND c.table_schema = :schema AND c.table_name = :relation
ORDER BY k.ordinal_position
"""


def _cell(value: Any) -> Any:
    """One value, as JSON, without pretending it is something it is not.

    `_plain` from the tool layer already answers for Decimal, datetime and UUID — money
    as a string so it keeps every digit, timestamps as ISO — and reusing it means the
    console and a generated document render the same row identically.

    What it has never had to answer for is bytes: the checkpoint tables store LangGraph
    state as bytea, and handing that to the JSON encoder raises UnicodeDecodeError on the
    first non-UTF-8 byte, which took the whole response down with a 500. A blob is not
    text and there is no honest way to show it in a table cell, so the cell says how big
    it is and stops.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes, binary>"
    return _plain(value)


@router.get("/rows/{schema}/{relation}")
def rows(
    schema: str,
    relation: str,
    ctx: Ctx = Depends(require_admin),
    limit: int = Query(default=DEFAULT_PAGE, ge=1, le=PAGE_CAP),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """One page of a relation's rows, with the total behind it.

    The schema and relation are caller-supplied, so they are resolved against
    information_schema before anything is built around them: a name that is not a
    relation in one of this application's spaces is 404, which is also the answer for a
    relation this console will not page. Same rule as everywhere else in the product — a
    caller who may not have something does not learn whether it exists.
    """
    if schema not in SPACES:
        raise _http(ToolError("not_found", "No such relation.", ""))

    with session_scope() as s:
        found = s.execute(
            text(
                "SELECT table_type FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = :relation"
            ),
            {"schema": schema, "relation": relation},
        ).scalar_one_or_none()
        if found is None:
            raise _http(ToolError("not_found", "No such relation.", ""))

        # 403 here, where the rest of the product says 404. The indistinguishability rule
        # protects things a caller must not learn exist, and this relation is listed in
        # the spaces panel with its row count and this same sentence attached. Hiding it
        # at the second step, having shown it at the first, would only look like a bug.
        refusal = _UNBROWSABLE.get((schema, relation))
        if refusal is not None:
            raise _http(ToolError("forbidden", refusal, ""))

        columns = s.execute(
            text(_COLUMNS_SQL), {"schema": schema, "relation": relation}
        ).mappings().all()
        key = s.execute(
            text(_KEY_SQL), {"schema": schema, "relation": relation}
        ).scalars().all()

        # Paging without an ORDER BY is paging over an order Postgres never promised, so
        # page 2 can repeat a row from page 1 and drop another entirely. The primary key
        # where there is one; the first column otherwise, which is the best a view can
        # offer. Either way the answer says which, so a reader can see how stable the
        # order they are looking at actually is.
        # Every column when there is no primary key, not just the first. Ordering
        # reporting.v_bookings by its first column put 200 rows into 25 groups and left
        # Postgres free to order within each one however it liked, so page 2 repeated
        # rows page 1 had already shown and dropped others entirely — while the response
        # said the order was stable. A full column list is not a guaranteed total order
        # either (two identical rows remain tied), but it is the strongest a view can
        # offer, and `ordered_by` tells the reader exactly what it got.
        ordered_by = list(key) or [c["column_name"] for c in columns]
        target = _qualified(schema, relation)
        order = ", ".join(_qualified_column(name) for name in ordered_by)

        total = int(s.execute(text(f"SELECT count(*) FROM {target}")).scalar_one())
        page = s.execute(
            text(f"SELECT * FROM {target} ORDER BY {order} LIMIT :limit OFFSET :offset"),
            {"limit": limit, "offset": offset},
        ).mappings().all()

    return {
        "schema": schema,
        "relation": relation,
        "kind": "view" if found == "VIEW" else "table",
        "columns": [{"name": c["column_name"], "type": c["data_type"]} for c in columns],
        "rows": [{k: _cell(v) for k, v in row.items()} for row in page],
        "total": total,
        "returned": len(page),
        "limit": limit,
        "offset": offset,
        # Stated on every response, not only when it bites: the reader should be able to
        # see the ceiling without having to hit it.
        "cap": PAGE_CAP,
        "cap_note": (
            f"At most {PAGE_CAP} rows per request; this page holds {len(page)} of "
            f"{total}. Nothing is dropped — page through with offset."
        ),
        "ordered_by": ordered_by,
    }


def _qualified_column(name: str) -> str:
    if not _IDENTIFIER.match(name):
        # Quoted rather than refused: a column named "group" or with capitals is legal
        # SQL and none of this module's business, whereas a schema or relation name that
        # surprises us means the catalogue lookup went wrong.
        return '"' + name.replace('"', '""') + '"'
    return name
