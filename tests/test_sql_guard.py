"""M2 verification — the SQL validator (pytest -m sql_guard).

Golden rule 3 in test form: single SELECT, allow-listed views only, enforced LIMIT and
timeout, read-only role.
"""

from __future__ import annotations

import pytest

from server.mcp import tools as T
from server.mcp.errors import ToolError
from server.mcp.sql_guard import ALLOWED_VIEWS, MAX_ROWS, validate

pytestmark = pytest.mark.sql_guard


NON_SELECT = [
    "DROP TABLE infinity.users",
    "DELETE FROM v_bookings",
    "UPDATE v_bookings SET status = 'confirmed'",
    "INSERT INTO v_bookings (user_id) VALUES ('u-mallory')",
    "TRUNCATE v_bookings",
    "ALTER TABLE infinity.users ADD COLUMN x int",
    "CREATE TABLE mallory (id int)",
    "GRANT SELECT ON infinity.users TO echomind_readonly",
    "SELECT * FROM v_bookings FOR UPDATE",
]

UNKNOWN_RELATIONS = [
    "SELECT * FROM infinity.users",
    "SELECT * FROM echomind.chunks",
    "SELECT * FROM echomind.actions",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM v_bookings JOIN infinity.users u ON u.id = v_bookings.user_id",
    "SELECT * FROM public.v_bookings",
    "SELECT * FROM v_secret_salaries",
]

DANGEROUS = [
    "SELECT pg_sleep(30)",
    "SELECT * FROM v_bookings WHERE user_id = (SELECT pg_read_file('/etc/passwd'))",
    "SELECT dblink('host=evil', 'SELECT 1')",
    "SELECT * FROM v_bookings WHERE pg_sleep(5) IS NULL",
    # With a valid FROM clause, so they are rejected for the function and not merely for
    # having no relation to read.
    "SELECT * FROM v_bookings WHERE lab_id = current_setting('is_superuser')",
    "SELECT * FROM v_bookings WHERE lab_id = version()",
    "SELECT * FROM v_bookings WHERE lab_id = current_user",
    "SELECT * FROM v_bookings WHERE lab_id = session_user",
    "SELECT * FROM v_bookings WHERE lab_id = current_database()",
    "SELECT * FROM v_bookings WHERE lab_id = inet_server_addr()",
    "SELECT * FROM v_bookings WHERE lab_id = pg_backend_pid()",
    "SELECT * FROM v_bookings WHERE lab_id = txid_current()",
    "SELECT * FROM v_bookings WHERE lab_id = lo_import('/etc/passwd')",
    "SELECT * FROM v_bookings WHERE lab_id = set_config('a', 'b', false)",
    "SELECT * FROM v_bookings WHERE lab_id = query_to_xml('x', true, true, '')",
    "SELECT * FROM v_bookings WHERE lab_id = pg_ls_dir('/')",
    "SELECT generate_series(1, 3) FROM v_bookings",
]

# A function in FROM position is not a Table node, so the view allow-list never sees it.
# The planner really did emit the first of these against a live database.
TABLE_FUNCTIONS = [
    "SELECT * FROM GET_USER_PROFILE('u-asha')",
    "SELECT * FROM generate_series(1, 10)",
    "SELECT * FROM v_bookings b JOIN generate_series(1,3) g ON true",
    "SELECT account_code FROM v_billing_lines WHERE account_code = "
    "(SELECT account_code FROM GET_USER_PROFILE('u-asha'))",
]

# Ordinary reporting SQL must keep working — an allow-list that blocks real queries is
# just a different kind of failure.
LEGITIMATE = [
    "SELECT sum(amount) AS total FROM v_billing_lines WHERE period = '2026-03'",
    "SELECT instrument, round(sum(amount), 2) AS t FROM v_billing_lines GROUP BY instrument",
    "SELECT to_char(starts_at, 'YYYY-MM') AS m, count(*) FROM v_bookings GROUP BY 1",
    "SELECT date_trunc('month', starts_at) AS m FROM v_bookings",
    "SELECT coalesce(instrument, 'n/a'), upper(status) FROM v_bookings",
    "SELECT lab_id, avg(scheduled_hours), max(tracked_hours) FROM v_usage_summary GROUP BY lab_id",
    "SELECT instrument, row_number() OVER (ORDER BY downtime_hours DESC) FROM v_instrument_downtime",
]


@pytest.mark.parametrize("sql", NON_SELECT)
def test_rejects_non_select(sql):
    with pytest.raises(ToolError) as exc:
        validate(sql, "admin")
    assert exc.value.code == "sql_rejected"


@pytest.mark.parametrize("sql", UNKNOWN_RELATIONS)
def test_rejects_unknown_relations(sql):
    with pytest.raises(ToolError) as exc:
        validate(sql, "admin")
    assert exc.value.code == "sql_rejected"


def test_rejection_names_the_allow_list():
    with pytest.raises(ToolError) as exc:
        validate("SELECT * FROM infinity.users", "admin")
    for view in ALLOWED_VIEWS:
        assert view in exc.value.hint


@pytest.mark.parametrize("sql", DANGEROUS)
def test_rejects_dangerous_functions(sql):
    with pytest.raises(ToolError) as exc:
        validate(sql, "admin")
    assert exc.value.code == "sql_rejected"


@pytest.mark.parametrize("sql", TABLE_FUNCTIONS)
def test_rejects_functions_used_as_a_table_source(sql):
    """Regression: a table function bypasses the relation allow-list entirely."""
    with pytest.raises(ToolError) as exc:
        validate(sql, "admin")
    assert exc.value.code == "sql_rejected"


@pytest.mark.parametrize("sql", LEGITIMATE)
def test_allows_ordinary_reporting_sql(sql):
    out = validate(sql, "admin")
    assert out.executed_sql


def test_unknown_functions_are_rejected_by_default():
    """Allow-list, not denylist: something nobody thought of is still refused."""
    with pytest.raises(ToolError):
        validate("SELECT some_new_extension_fn(amount) FROM v_billing_lines", "admin")


def test_rejects_multiple_statements():
    with pytest.raises(ToolError) as exc:
        validate("SELECT * FROM v_bookings; DROP TABLE infinity.users", "admin")
    assert "Multiple statements" in exc.value.message


def test_rejects_cte():
    with pytest.raises(ToolError) as exc:
        validate("WITH x AS (SELECT * FROM v_bookings) SELECT * FROM x", "admin")
    assert exc.value.code == "sql_rejected"


def test_rejects_unparseable_sql():
    with pytest.raises(ToolError):
        validate("SELECT FROM WHERE ((((", "admin")


def test_rejects_empty_sql():
    with pytest.raises(ToolError):
        validate("", "admin")


# --- LIMIT injection ----------------------------------------------------------------


def test_injects_limit_when_absent():
    out = validate("SELECT * FROM v_bookings", "admin")
    assert f"LIMIT {MAX_ROWS}" in out.executed_sql


def test_clamps_oversized_limit():
    out = validate("SELECT * FROM v_bookings LIMIT 100000", "admin")
    assert f"LIMIT {MAX_ROWS}" in out.executed_sql
    assert "100000" not in out.executed_sql


def test_preserves_smaller_limit():
    out = validate("SELECT * FROM v_bookings LIMIT 5", "admin")
    assert out.executed_sql.rstrip().endswith("LIMIT 5")


def test_replaces_non_literal_limit():
    out = validate("SELECT * FROM v_bookings LIMIT (SELECT 9999)", "admin")
    assert f"LIMIT {MAX_ROWS}" in out.executed_sql


# --- role handling ------------------------------------------------------------------


def test_plain_user_cannot_reach_the_validator():
    with pytest.raises(ToolError):
        validate("SELECT * FROM v_bookings", "user")


def test_pi_query_is_rewritten_with_a_lab_filter():
    out = validate("SELECT * FROM v_billing_lines", "pi", ("lab-a",))
    assert out.lab_filtered is True
    assert "lab_id IN ('lab-a')" in out.executed_sql


def test_admin_query_is_not_rewritten():
    out = validate("SELECT * FROM v_billing_lines", "admin")
    assert out.lab_filtered is False
    assert "lab_id IN" not in out.executed_sql


def test_downtime_view_has_no_lab_dimension_so_is_not_rewritten():
    out = validate("SELECT * FROM v_instrument_downtime", "pi", ("lab-a",))
    assert out.lab_filtered is False


def test_pi_with_no_labs_sees_nothing_rather_than_everything():
    out = validate("SELECT * FROM v_bookings", "pi", ())
    assert "lab_id IN (NULL)" in out.executed_sql


# --- executed against the real read-only role ---------------------------------------


def test_injected_limit_actually_caps_rows(ctxs):
    out = T.run_readonly_sql(ctxs["cora"], sql="SELECT * FROM v_bookings")
    assert out["row_count"] <= MAX_ROWS


def test_readonly_role_is_the_executing_role(ctxs):
    out = T.run_readonly_sql(ctxs["cora"], sql="SELECT * FROM v_bookings LIMIT 1")
    assert out["row_count"] == 1
    # A write attempt is rejected by the validator long before the role would refuse it.
    with pytest.raises(ToolError):
        T.run_readonly_sql(ctxs["cora"], sql="DELETE FROM v_bookings")


def test_statement_timeout_is_enforced(ctxs):
    """pg_sleep is blocked by the validator; the timeout is the independent backstop."""
    from sqlalchemy import text

    from server.db import ro_session

    with ro_session() as s:
        assert s.execute(text("SHOW statement_timeout")).scalar_one() in ("5s", "5000ms")
