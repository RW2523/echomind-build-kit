"""SQL validation and rewriting for tool 11 (run_readonly_sql).

Golden rule 3: single SELECT, allow-listed views only, enforced LIMIT and timeout,
read-only DB role. No exceptions, including tests.

This module only decides what SQL may run. The read-only role and the `reporting`
schema grants (003_roles.sql) are the independent second line of defence: even a total
failure here cannot reach a base table or write anything.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from server.mcp.errors import sql_rejected

log = logging.getLogger("echomind.sql_guard")

DIALECT = "postgres"
MAX_ROWS = 200

# The only relations tool 11 may touch.
ALLOWED_VIEWS = frozenset(
    {"v_bookings", "v_usage_summary", "v_billing_lines", "v_instrument_downtime"}
)

# Views carrying a lab dimension. v_instrument_downtime is deliberately absent: it holds
# no per-lab or per-user data (instrument, facility, month, downtime, repair count), so
# there is nothing to scope a PI to and no filter to apply.
LAB_SCOPED_VIEWS = frozenset({"v_bookings", "v_usage_summary", "v_billing_lines"})

# Function policy, in two parts.
#
# sqlglot gives every construct it *recognises* its own node type — Sum, Cast, And,
# DateTrunc and so on — and lumps everything it does not recognise into Anonymous. That
# split is the security boundary we want: pg_sleep, pg_read_file, dblink, current_setting
# and every extension function are unmodelled, so they all arrive as Anonymous. Modelled
# nodes are standard SQL operators and functions, none of which can read a file or sleep.
#
#   * Anonymous  -> rejected unless explicitly named below (currently nothing needs to be)
#   * modelled   -> allowed, except the handful named in DENIED_MODELLED
#
# This keeps ordinary reporting SQL working (WHERE ... AND ... would otherwise be
# rejected, because sqlglot models And as a Func) without having to enumerate every
# operator sqlglot happens to represent that way.
ANONYMOUS_ALLOWED: frozenset[str] = frozenset()

# Modelled nodes that are safe by construction but still have no business in a
# reporting query: they either leak environment detail or fabricate rows. Names are
# sqlglot's canonical ones (version() -> current_version, unnest() -> explode).
DENIED_MODELLED = frozenset(
    {
        "current_version", "current_user", "session_user",
        "current_schema", "current_database",
        "generate_series", "exploding_generate_series", "explode", "unnest",
        "posexplode", "sequence",
    }
)

# Retained for the error message and for documentation of intent.
ALLOWED_FUNCTIONS = frozenset(
    {
        # aggregates
        "sum", "count", "avg", "min", "max", "stddev", "variance",
        # numeric
        "round", "abs", "ceil", "ceiling", "floor", "trunc", "sqrt", "power", "mod", "div",
        # null / conditional
        "coalesce", "nullif", "greatest", "least", "case", "cast", "convert", "if",
        # string
        "lower", "upper", "length", "trim", "ltrim", "rtrim", "substring", "substr",
        "concat", "concat_ws", "replace", "split_part", "left", "right", "position",
        "initcap", "lpad", "rpad", "strpos",
        # date / time
        "date_trunc", "date_part", "extract", "to_char", "to_date", "to_timestamp",
        "age", "now", "current_date", "current_timestamp", "make_interval", "justify_hours",
        # ordering / windowing helpers that are read-only by nature
        "row_number", "rank", "dense_rank", "ntile", "lag", "lead",
        "first_value", "last_value", "over", "partition",
        # type helpers sqlglot emits
        "anyvalue", "distinct",
        # sqlglot canonicalises several Postgres functions to its own internal names;
        # these are what the checker actually sees for to_char, date_trunc, to_date,
        # to_timestamp, position and lpad/rpad respectively.
        "time_to_str", "timestamp_trunc", "str_to_date", "str_to_time",
        "str_position", "pad",
    }
)

# Node types that must never appear anywhere in the tree.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.TruncateTable,
    exp.Grant, exp.Command, exp.Transaction, exp.Commit, exp.Rollback, exp.Use,
    exp.Set, exp.Into, exp.Lock, exp.Merge,
)


@dataclass(frozen=True)
class ValidatedSQL:
    executed_sql: str
    relations: tuple[str, ...]
    lab_filtered: bool


def _reject(message: str, hint: str = "") -> None:
    raise sql_rejected(message, hint)


def _allow_list_hint() -> str:
    return "Allowed relations: " + ", ".join(sorted(ALLOWED_VIEWS)) + "."


def _check_no_forbidden_nodes(tree: exp.Expression) -> None:
    for node_type in FORBIDDEN_NODES:
        found = tree.find(node_type)
        if found is not None:
            _reject(
                f"Only a single plain SELECT is allowed; found {node_type.__name__.upper()}.",
                "Rewrite the request as a read-only SELECT over the reporting views.",
            )
    if tree.find(exp.With) is not None:
        # "any node type other than a plain SELECT" — read the rule conservatively and
        # fail closed rather than reason about which CTEs are write-free.
        _reject(
            "Common table expressions (WITH ...) are not allowed.",
            "Inline the CTE as a subquery or a plain SELECT.",
        )


def _func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous) and isinstance(node.this, str):
        return node.this.lower()
    try:
        return (node.sql_name() or "").lower()
    except Exception:
        return type(node).__name__.lower()


def _check_functions(tree: exp.Expression) -> None:
    for node in tree.find_all(exp.Func):
        name = _func_name(node)
        unknown = isinstance(node, exp.Anonymous)
        if (unknown and name not in ANONYMOUS_ALLOWED) or name in DENIED_MODELLED:
            _reject(
                f"Function {name}() is not allowed.",
                "Only standard aggregates, arithmetic, string and date functions may be "
                "used over the reporting views.",
            )


def _check_table_sources(tree: exp.Expression) -> None:
    """Reject function calls used as a table source.

    `SELECT * FROM some_function('x')` never reaches `_check_relations`, because a table
    function parses as a Func rather than a Table — so the view allow-list would not see
    it at all. Only a named relation or a subquery may sit in FROM/JOIN.
    """
    for holder in list(tree.find_all(exp.From)) + list(tree.find_all(exp.Join)):
        source = holder.this
        if source is None:
            continue
        if isinstance(source, (exp.Table, exp.Subquery)):
            continue
        _reject(
            "Only the allow-listed views and subqueries may appear in FROM or JOIN; "
            f"found {type(source).__name__.upper()}.",
            _allow_list_hint(),
        )


def _relations(tree: exp.Expression) -> list[str]:
    return [t.name.lower() for t in tree.find_all(exp.Table) if t.name]


def _check_relations(tree: exp.Expression) -> tuple[str, ...]:
    names = _relations(tree)
    if not names:
        _reject("The query does not read any relation.", _allow_list_hint())
    for name in names:
        if name not in ALLOWED_VIEWS:
            _reject(f"Relation '{name}' is not allow-listed.", _allow_list_hint())
    # A schema qualifier could point the same view name at another schema.
    for table in tree.find_all(exp.Table):
        if table.db and table.db.lower() != "reporting":
            _reject(
                f"Schema '{table.db}' is not allow-listed.",
                _allow_list_hint(),
            )
    return tuple(sorted(set(names)))


def _apply_limit(select: exp.Select) -> exp.Select:
    """Enforce a LIMIT of at most MAX_ROWS, injecting one when absent."""
    limit = select.args.get("limit")
    if limit is None:
        return select.limit(MAX_ROWS)
    try:
        current = int(limit.expression.name)
    except (AttributeError, ValueError):
        # Non-literal LIMIT (parameter, expression) — replace it with the hard cap.
        return select.limit(MAX_ROWS)
    return select if current <= MAX_ROWS else select.limit(MAX_ROWS)


def _restrict_to_labs(
    tree: exp.Expression, lab_ids: tuple[str, ...]
) -> tuple[exp.Expression, bool]:
    """Replace each lab-scoped view reference with a pre-filtered subquery.

    Rewriting at the table reference (rather than appending to WHERE) is what makes the
    restriction hold for aggregates, GROUP BY, joins, subqueries and aliases alike:
    the PI's query simply cannot observe a row from another lab, whatever its shape.
    """
    if not lab_ids:
        # A PI with no labs can see nothing rather than everything.
        values = "(NULL)"
    else:
        escaped = ", ".join("'" + lab.replace("'", "''") + "'" for lab in lab_ids)
        values = f"({escaped})"

    applied = False

    def transform(node: exp.Expression) -> exp.Expression:
        nonlocal applied
        if isinstance(node, exp.Table) and node.name.lower() in LAB_SCOPED_VIEWS:
            if node.parent and isinstance(node.parent, exp.Subquery) and node.parent.args.get(
                "_echomind_filtered"
            ):
                return node
            applied = True
            alias = node.alias_or_name
            inner = sqlglot.parse_one(
                f"SELECT * FROM {node.name} WHERE lab_id IN {values}", read=DIALECT
            )
            sub = exp.Subquery(this=inner)
            sub.set("alias", exp.TableAlias(this=exp.to_identifier(alias)))
            sub.set("_echomind_filtered", True)
            return sub
        return node

    return tree.transform(transform, copy=True), applied


def validate(sql: str, role: str, lab_ids: tuple[str, ...] = ()) -> ValidatedSQL:
    """Validate, rewrite and return the exact SQL to execute. Raises ToolError."""
    if not sql or not sql.strip():
        _reject("Empty SQL.", _allow_list_hint())

    # sqlglot tolerates a trailing semicolon; anything after it is a second statement.
    if re.search(r";\s*\S", sql.strip()):
        _reject(
            "Multiple statements are not allowed.",
            "Send exactly one SELECT.",
        )

    try:
        statements = sqlglot.parse(sql, read=DIALECT)
    except Exception as exc:
        _reject(f"Could not parse the SQL: {exc}", "Send one syntactically valid SELECT.")

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        _reject("Multiple statements are not allowed.", "Send exactly one SELECT.")

    tree = statements[0]
    if not isinstance(tree, exp.Select):
        _reject(
            "Only a single plain SELECT is allowed.",
            "Rewrite the request as a read-only SELECT over the reporting views.",
        )

    _check_no_forbidden_nodes(tree)
    _check_table_sources(tree)
    _check_functions(tree)
    relations = _check_relations(tree)

    lab_filtered = False
    if role == "pi":
        tree, lab_filtered = _restrict_to_labs(tree, lab_ids)
    elif role != "admin":
        # Spec 02: tool 11 is T2/T3. Tier is checked in the handler; this is a backstop.
        _reject("run_readonly_sql requires the PI or admin role.", "")

    tree = _apply_limit(tree)
    return ValidatedSQL(
        executed_sql=tree.sql(dialect=DIALECT),
        relations=relations,
        lab_filtered=lab_filtered,
    )
