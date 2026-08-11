"""The data branch: plan -> execute -> answer strictly from returned rows.

Golden rule 1 lives here. The model chooses *which* question to ask the platform and
writes the prose, but every value in the reply must appear in the rows that came back.
A draft containing a number the rows do not contain is discarded in favour of a
deterministic rendering — the model does not get to invent a figure and have it printed.
"""

from __future__ import annotations

import contextlib
import logging
import re
from decimal import Decimal
from typing import Any

from server.agent.llm import chat, chat_json
from server.agent.responses import AgentResponse
from server.auth import Ctx
from server.mcp import tools as tools_mod
from server.mcp.errors import ToolError, forbidden

log = logging.getLogger("echomind.data")

# Fields that belong to the plan, not to the tool call. Kept in one place because the
# entitlement check and the dispatcher must agree on where to find them.
PLAN_LEVEL_KEYS = ("subject_user_id",)

VIEW_SCHEMA = """\
v_bookings(user_id, user_name, lab_id, instrument, facility, starts_at, ends_at, status)
v_usage_summary(lab_id, user_id, instrument, month, scheduled_hours, tracked_hours)
v_billing_lines(account_code, lab_id, period, description, instrument, amount)
v_instrument_downtime(instrument, facility, month, downtime_hours, repair_count)"""

# Tools the data branch may plan with, by tier. Tool 11 is T2/T3 only; the handler
# enforces that too, this simply stops the planner proposing something doomed.
TOOL_MENU = """get_my_bookings(date_from?, date_to?)        the caller's own bookings
get_usage_records(scope, id?, month?)        scope user|lab|instrument; scheduled vs tracked
get_request_status(request_id?, mine?)       service request status and history
track_sample(barcode?, sample_id?)           sample state timeline
get_billing_summary(account_code, period)    invoice total and lines, period is YYYY-MM
get_user_profile(user_id?)                   role, lab, training, account codes
get_facility_catalog(facility_id?)           facilities, instruments, rates, templates
check_availability(instrument_id, date_from, date_to)   free slots
get_instrument_health(instrument_id)         instrument status"""

PLANNER_SYSTEM = """You plan how to answer a question about the Infinity X platform using
real records. You never answer from your own knowledge.

Reply only as JSON.

Available tools:
{menu}
{sql_section}
Choose a tool call:
  {{"mode": "tool", "tool": "<name>", "arguments": {{...}}}}
{sql_option}
Always include "subject_user_id" when the question is about a NAMED person:
  {{"mode": "tool", "tool": "...", "arguments": {{...}}, "subject_user_id": "u-alice"}}
User ids have the form u-<firstname>, all lowercase (Alice -> u-alice).
Use the caller's own id when the question says "my", "me" or "I".

Rules:
- Never silently answer about the caller when the question named someone else. Name them
  in subject_user_id and let the server decide whether access is permitted.
- Tool names are not SQL functions: never write a tool name inside SQL.
{sql_rules}
- Prefer a direct tool for anything about the caller's own records ("my bookings",
  "my requests", "my invoice"). Tools are cheaper and already scoped to the caller.
- If the question asks for a total, sum, count or average, compute it IN SQL with an
  aggregate. Do not return raw rows and expect them to be added up afterwards. When a
  breakdown is also useful, group by the breakdown column so each row carries its own
  amount.
- When the question asks WHY a charge or an amount occurred, return the breakdown that
  explains it: group v_billing_lines by instrument with sum(amount), so each component
  total is visible as its own row. If the question quotes a specific amount, the grouping
  must be fine enough that the amount appears as a row rather than being buried in a
  grand total.
- SQL is already restricted to the caller's labs by the server. Do not try to look up
  the caller's lab or account code — filter on what the question actually asks for.
- Lab identifiers are literals like 'lab-a'. Account codes look like 'ACC-A1'.
- Today's reference date in this dataset is 2026-03-31. Periods and months are 'YYYY-MM'.
- Return the JSON and nothing else."""

SQL_SECTION = """
Read-only SQL is also available, over exactly these four views:
{schema}
"""

SQL_RULES = """- Pick ONE mode. In sql mode the only relations that exist are the four views above.
  No other table, no function-as-table, no CTE, no subquery against anything else."""

# Callers without SQL rights are never shown the SQL mode, so proposing it wastes a
# round-trip and used to produce a wrong tool substitution.
NO_SQL_RULE = """- There is no SQL mode available to you. Every question must be answered
  with one of the tools above. An invoice total is get_billing_summary(account_code,
  period); a usage rollup is get_usage_records."""

SQL_OPTION = """or a single read-only SELECT:
  {"mode": "sql", "sql": "SELECT ..."}
"""

ANSWER_SYSTEM = """You state what the records show, for a working scientist.

Absolute rules:
1. Every number, date, name and status in your reply must appear in the ROWS given to
   you. You may not estimate, round, or infer any value that is not there.
2. The one exception: you may state the exact total of a numeric column when the
   question asks for a total. Any total you give is recomputed and checked against the
   rows, so give it exactly or not at all.
3. If the rows do not answer the question, say exactly what they do show instead. When a
   RESULT FACT already answers the question directly (for example
   requested_window_free = True), lead with that rather than reinterpreting the rows.
4. Write every value exactly as the rows spell it, including decimal places, and with
   no thousands separators. If a row says 412.00, write $412.00 — never $412, even if
   the question wrote it that way. If a row says 5514.50, never write 5,514.50.
5. RESULT FACTS describe the whole result set, not each row. If it says count = 20,
   there are 20 in total; do not multiply it by the number of rows shown.
6. Two or three sentences. No preamble, no apology, no "based on the data".
7. Do not describe the query or the tool. Describe the answer."""

NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# For rewriting, match only standalone numbers. Without the lookarounds the canonicaliser
# treats "14:00" as the two numbers 14 and 00, and rewriting 00 to a row's "0" turns a
# time into "14:0". Dates and times are compound tokens, not quantities.
CANONICAL_NUMBER_RE = re.compile(r"(?<![\d:.\-/])\d[\d,]*(?:\.\d+)?(?![\d:\-/])")


def _instrument_catalog() -> str:
    """Instrument ids for the planner. Tools take ids, and a model cannot guess them."""
    from sqlalchemy import text as sql_text

    from server.db import session_scope

    with session_scope() as s:
        rows = s.execute(
            sql_text("SELECT id, name FROM infinity.instruments ORDER BY name")
        ).all()
    return "\n".join(f"  {i} = {n}" for i, n in rows)


def _plan(question: str, ctx: Ctx, history: str = "") -> dict[str, Any]:
    may_sql = ctx.is_admin or ctx.is_pi
    system = PLANNER_SYSTEM.format(
        menu=TOOL_MENU,
        sql_section=SQL_SECTION.format(schema=VIEW_SCHEMA) if may_sql else "",
        sql_option=SQL_OPTION if may_sql else "",
        sql_rules=SQL_RULES if may_sql else NO_SQL_RULE,
    )
    system += ("\n\nInstrument ids (tools take the id, never the display name):\n"
               + _instrument_catalog())
    plan = chat_json(
        [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Caller user_id: {ctx.user_id}\n"
                    f"Caller role: {ctx.role}\n"
                    f"Caller labs: {', '.join(ctx.lab_ids) or 'none'}\n"
                    + (f"\n{history}\n" if history else "")
                    + f"\nQUESTION: {question}"
                ),
            },
        ],
        default={"mode": "tool", "tool": "get_my_bookings", "arguments": {}},
        max_tokens=300,
    )
    if plan.get("mode") == "sql" and not may_sql:
        # The planner proposed SQL for a caller who has no SQL rights. Substituting a
        # fixed tool here (which this used to do) answers a billing question with
        # bookings — worse than useless, because the reply looks confident. Re-ask with
        # the refusal made explicit instead, and only then give up.
        log.info("planner proposed SQL for a %s; re-planning tool-only", ctx.role)
        plan = chat_json(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": (
                        f"Caller user_id: {ctx.user_id}\n"
                        f"Caller role: {ctx.role}\n"
                        f"Caller labs: {', '.join(ctx.lab_ids) or 'none'}\n"
                        + (f"\n{history}\n" if history else "")
                        + f"\nQUESTION: {question}\n\n"
                        "You proposed SQL. This caller may not run SQL. Choose the TOOL "
                        "from the list that answers the question — for an invoice total "
                        "that is get_billing_summary(account_code, period)."
                    ),
                },
            ],
            default={},
            max_tokens=300,
        )
        if plan.get("mode") == "sql" or not plan.get("tool"):
            raise forbidden()
    return plan


def _numbers_in(value: Any) -> set[Decimal]:
    out: set[Decimal] = set()
    for token in NUMBER_RE.findall(str(value)):
        try:
            out.add(Decimal(token.replace(",", "")))
        except Exception:
            continue
    return out


def column_totals(rows: list[dict]) -> dict[str, Decimal]:
    """Sum each purely-numeric column, in Python.

    A total is the one derived value a data answer legitimately needs, and asking the
    model for it would mean trusting an LLM's arithmetic. Computing it here keeps the
    guarantee intact in both directions: the correct total is accepted, and a wrong one
    is still rejected, because we did the addition, not the model.
    """
    if not rows:
        return {}
    totals: dict[str, Decimal] = {}
    for column in rows[0]:
        values = []
        for row in rows:
            value = row.get(column)
            if isinstance(value, bool) or value is None:
                values = []
                break
            if isinstance(value, (int, float, Decimal)):
                values.append(Decimal(str(value)))
            else:
                values = []
                break
        if values:
            totals[column] = sum(values, Decimal(0))
    return totals


def _allowed_numbers(
    rows: list[dict], question: str, scalars: dict[str, Any] | None = None
) -> set[Decimal]:
    """Every value the reply is permitted to contain."""
    allowed: set[Decimal] = {Decimal(len(rows))}
    for row in rows:
        for value in row.values():
            allowed |= _numbers_in(value)
    for value in (scalars or {}).values():
        allowed |= _numbers_in(value)
    # Numbers the user themselves supplied (a month, a year, an id) are fair to echo.
    allowed |= _numbers_in(question)
    # Verified aggregates over the returned rows.
    allowed |= set(column_totals(rows).values())

    # Accept equivalent spellings: 412 for 412.00, and 2dp rounding of long decimals.
    widened: set[Decimal] = set()
    for n in allowed:
        widened.add(n)
        widened.add(n.normalize())
        try:
            widened.add(n.quantize(Decimal("0.01")))
            widened.add(Decimal(int(n)))
        except Exception:
            pass
    return widened


def verify_numbers(
    draft: str, rows: list[dict], question: str, scalars: dict[str, Any] | None = None
) -> list[str]:
    """Return the numeric tokens in `draft` that no row supports."""
    allowed = _allowed_numbers(rows, question, scalars)
    offenders = []
    for token in NUMBER_RE.findall(draft):
        try:
            value = Decimal(token.replace(",", ""))
        except Exception:
            continue
        if not any(value == a or value.normalize() == a.normalize() for a in allowed):
            offenders.append(token)
    return offenders


def _render_rows(rows: list[dict], limit: int = 10) -> str:
    if not rows:
        return ""
    columns = list(rows[0])
    lines = [" | ".join(columns), "-|-".join("-" * len(c) for c in columns)]
    for row in rows[:limit]:
        lines.append(" | ".join(str(row[c]) for c in columns))
    if len(rows) > limit:
        lines.append(f"... and {len(rows) - limit} more rows")
    return "\n".join(lines)


def _deterministic_answer(rows: list[dict], question: str) -> str:
    """Guaranteed-faithful fallback: no prose beyond the framing, values verbatim."""
    if not rows:
        return "The records contain no rows matching that question."
    if len(rows) == 1 and len(rows[0]) == 1:
        (key, value), = rows[0].items()
        return f"The records show {key} = {value}."
    return (
        f"The records return {len(rows)} row(s). Here they are exactly as stored:\n\n"
        f"{_render_rows(rows)}"
    )


def canonicalize_numbers(text: str, rows: list[dict], scalars: dict[str, Any]) -> str:
    """Rewrite each number in the draft to the exact spelling the record uses.

    "$412" becomes "$412.00" and "5,514.50" becomes "5514.50", deterministically, because
    the row says so. Asking the model to copy decimals faithfully worked most of the time;
    doing it in code works every time, and "the answer spells values the way the record
    does" is the same principle the rest of this module enforces.
    """
    spellings: dict[Decimal, str] = {}

    def note(value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        raw = str(value)
        if not NUMBER_RE.fullmatch(raw.strip()):
            return
        # A value that will not parse as a decimal simply has no canonical spelling.
        with contextlib.suppress(Exception):
            spellings.setdefault(Decimal(raw.strip().replace(",", "")), raw.strip())

    for row in rows:
        for value in row.values():
            note(value)
    for value in scalars.values():
        note(value)
    for total in column_totals(rows).values():
        spellings.setdefault(total, str(total))

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        try:
            value = Decimal(token.replace(",", ""))
        except Exception:
            return token
        canonical = spellings.get(value)
        return canonical if canonical and canonical != token else token

    return CANONICAL_NUMBER_RE.sub(replace, text)


def answer_from_rows(
    question: str,
    rows: list[dict],
    columns: list[str],
    scalars: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    """Return (text, model_written). Falls back to a deterministic rendering if the
    model's draft contains a value the rows do not support."""
    scalars = scalars or {}
    if not rows:
        return (
            "I found no records matching that. If you expected some, check the period "
            "or the account code — I can only report what the platform has stored.",
            False,
        )

    totals = column_totals(rows)
    context = (f"QUESTION:\n{question}\n\nROWS ({len(rows)} returned):\n"
               f"{_render_rows(rows, limit=25)}")
    if scalars:
        context += "\n\nRESULT FACTS (about the whole result, not per row):\n" + "\n".join(
            f"  {k} = {v}" for k, v in scalars.items()
        )
    if totals:
        context += "\n\nVERIFIED COLUMN TOTALS (computed from the rows above):\n" + "\n".join(
            f"  sum({k}) = {v}" for k, v in totals.items()
        )

    draft = chat(
        [
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": context},
        ],
        temperature=0.0,
        max_tokens=350,
    )

    draft = canonicalize_numbers(draft.strip(), rows, scalars)

    offenders = verify_numbers(draft, rows, question, scalars)
    if offenders:
        log.warning(
            "draft contained unsupported values %s; using deterministic rendering",
            offenders,
        )
        return _deterministic_answer(rows, question), False
    return draft, True


def _normalise_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Lift plan-level keys out of `arguments`, where the model sometimes buries them.

    `subject_user_id` is a plan-level field: the entitlement check reads it from the top
    level, and dispatch passes `arguments` to the tool. Those two readings disagreeing is
    the whole problem — a plan with the key nested inside `arguments` skipped
    _assert_may_read_subject entirely and then splatted an unexpected kwarg into the
    handler. What saved us was the accident that no tool happens to have a parameter of
    that name; a tool that did would have run unchecked. Normalising to one location
    means the check cannot be routed around by where the model chose to put the key.
    """
    arguments = plan.get("arguments")
    if not isinstance(arguments, dict):
        return plan
    for key in PLAN_LEVEL_KEYS:
        if key in arguments:
            plan.setdefault(key, arguments.pop(key))
            log.info("plan had %s nested inside arguments; lifted to plan level", key)
    return plan


def _assert_may_read_subject(plan: dict[str, Any], ctx: Ctx) -> None:
    """If the question is about someone else, run the entitlement check before anything.

    Without this, a planner that quietly picks `get_my_bookings` for "show me alice's
    bookings" would answer with the *caller's* rows — no data leak, but also no tier
    denial, when spec 05 requires the caller be told they are not permitted. Reuses
    get_user_profile so there is one entitlement rule, not a second copy of it.
    """
    subject = plan.get("subject_user_id")
    if not subject or not isinstance(subject, str) or subject == ctx.user_id:
        return
    tools_mod.get_user_profile(ctx, user_id=subject)  # raises forbidden / not_found


def answer(question: str, ctx: Ctx, history: str = "") -> AgentResponse:
    plan = _normalise_plan(_plan(question, ctx, history))
    log.info("data plan: %s", plan)

    executed_sql: str | None = None
    try:
        _assert_may_read_subject(plan, ctx)
        if plan.get("mode") == "sql":
            rows, columns, executed_sql = _run_sql(plan.get("sql", ""), ctx, question)
            scalars: dict[str, Any] = {}
        else:
            rows, columns, scalars = _run_tool(plan, ctx)
    except ToolError as exc:
        if exc.code == "forbidden":
            return AgentResponse(
                response_type="redirect",
                route="data",
                text=(
                    f"{exc.message} I can only show you records you are entitled to see, "
                    "and this is not one of them. "
                    f"{exc.hint}"
                ),
                meta={"error": exc.to_dict()["error"]},
            )
        return AgentResponse(
            response_type="redirect",
            route="data",
            text=f"I could not run that lookup. {exc.message} {exc.hint}".strip(),
            meta={"error": exc.to_dict()["error"]},
        )
    except Exception:
        # Anything not already a ToolError is a bug on our side, and its message is not
        # fit for a user: a bare TypeError put "get_my_bookings() got an unexpected
        # keyword argument" on screen where a refusal belonged. Log the detail, show a
        # plain sentence, and never surface internals.
        log.exception("data branch failed on plan %s", plan)
        return AgentResponse(
            response_type="redirect",
            route="data",
            text=(
                "I could not complete that lookup — something went wrong on my side, so "
                "I would rather tell you than show you a half-answer. Please try "
                "rephrasing, or ask the core facility admin."
            ),
            meta={"error": {"code": "internal_error"}},
        )

    text, model_written = answer_from_rows(question, rows, columns, scalars)
    return AgentResponse(
        response_type="rows_answer",
        text=text,
        rows=rows,
        columns=columns,
        executed_sql=executed_sql,
        route="data",
        meta={"model_written": model_written, "plan": plan, "result_facts": scalars},
    )


def _run_sql(sql: str, ctx: Ctx, question: str) -> tuple[list[dict], list[str], str]:
    """Validate and run, with exactly one silent repair attempt (spec 04 §3)."""
    try:
        result = tools_mod.run_readonly_sql(ctx, sql=sql)
    except ToolError as first:
        if first.code != "sql_rejected":
            raise
        log.info("sql rejected (%s); attempting one repair", first.message)
        repaired = chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Your SQL was rejected. Return corrected SQL as JSON "
                        '{"sql": "SELECT ..."}. One plain SELECT, no CTEs, no writes, '
                        "only these views:\n" + VIEW_SCHEMA
                    ),
                },
                {
                    "role": "user",
                    "content": (f"QUESTION: {question}\nSQL: {sql}\n"
                                f"ERROR: {first.message} {first.hint}"),
                },
            ],
            default={"sql": ""},
            max_tokens=300,
        )
        result = tools_mod.run_readonly_sql(ctx, sql=repaired.get("sql", ""))

    return result["rows"], result["columns"], result["executed_sql"]


def _run_tool(plan: dict, ctx: Ctx) -> tuple[list[dict], list[str], dict[str, Any]]:
    name = plan.get("tool") or "get_my_bookings"
    arguments = plan.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    result = tools_mod.call(ctx, name, arguments)
    return _rows_from_tool_result(result)


def _rows_from_tool_result(result: dict) -> tuple[list[dict], list[str], dict[str, Any]]:
    """Flatten a tool result into (rows, columns, scalars).

    Scalars are returned *alongside* the rows, never merged into them. Merging a scalar
    like `count: 20` into all 20 rows made the column total 400, which then counted as a
    verified value — and the agent duly reported "you have 400 bookings". A per-result
    fact and a per-row fact are different things and have to stay that way.
    """
    for key in ("bookings", "rows", "requests", "lines", "free_slots", "history",
                "instruments", "members"):
        value = result.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            scalars = {
                k: v for k, v in result.items()
                if isinstance(v, (str, int, float)) and k != key
            }
            if isinstance(result.get("totals"), dict):
                scalars.update(result["totals"])
            return value, list(value[0]), scalars

    flat = {k: v for k, v in result.items() if isinstance(v, (str, int, float, type(None)))}
    if isinstance(result.get("totals"), dict):
        flat.update(result["totals"])
    if not flat:
        return [], [], {}
    return [flat], list(flat), {}
