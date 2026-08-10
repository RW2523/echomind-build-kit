"""M1 verification — seeded volumes and the read-only role's privileges."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

pytestmark = pytest.mark.seed_counts

# Exactly the volumes stated in spec 01.
EXPECTED_COUNTS = {
    "infinity.facilities": 3,
    "infinity.instruments": 12,
    "infinity.labs": 6,
    "infinity.users": 25,
    "infinity.bookings": 200,
    "infinity.usage_records": 500,
    "infinity.request_templates": 8,
    "infinity.service_requests": 40,
    "infinity.projects": 4,
    "infinity.maintenance_events": 60,
}

# Approving a booking, a service request or an onboarding writes to these three tables,
# so a bare count(*) here measures "seed volume + whatever anyone has since done" — and
# went red the moment the demo booked an instrument. The seed mints readable ids
# (bk-0007, u-alice); the application mints 8 hex characters (bk-0babe5a3). Excluding
# the application's shape keeps the assertion exact rather than relaxing it to >=,
# which would have stopped catching an under-seeded table.
APPEND_ONLY_TABLES = {
    "infinity.bookings",
    "infinity.service_requests",
    "infinity.users",
}
APP_MINTED_ID = "-[0-9a-f]{8}$"

ALLOW_LISTED_VIEWS = [
    "v_bookings",
    "v_usage_summary",
    "v_billing_lines",
    "v_instrument_downtime",
]


@pytest.mark.parametrize("relation,expected", EXPECTED_COUNTS.items())
def test_seeded_row_counts(db, relation, expected):
    where = f" WHERE id !~ '{APP_MINTED_ID}'" if relation in APPEND_ONLY_TABLES else ""
    count = db.execute(text(f"SELECT count(*) FROM {relation}{where}")).scalar_one()
    assert count == expected


def test_rows_the_application_created_are_not_counted_as_seed(db):
    """The exclusion above must actually exclude something once the demo has run.

    If the discriminator silently stopped matching — an id format change, say — the
    counts would quietly go back to measuring seed-plus-activity and start failing for
    reasons that have nothing to do with seeding.
    """
    total = db.execute(text("SELECT count(*) FROM infinity.bookings")).scalar_one()
    seeded = db.execute(
        text(f"SELECT count(*) FROM infinity.bookings WHERE id !~ '{APP_MINTED_ID}'")
    ).scalar_one()
    assert seeded == EXPECTED_COUNTS["infinity.bookings"]
    assert total >= seeded, "app-created bookings can only add to the seeded set"


def test_three_monthly_invoice_periods(db):
    periods = db.execute(
        text("SELECT DISTINCT period FROM infinity.invoices ORDER BY period")
    ).scalars().all()
    assert periods == ["2026-01", "2026-02", "2026-03"]


def test_invoice_totals_match_their_lines(db):
    drift = db.execute(
        text(
            """SELECT count(*) FROM infinity.invoices i
               WHERE i.total <> (SELECT COALESCE(sum(amount), 0)
                                 FROM infinity.invoice_lines WHERE invoice_id = i.id)"""
        )
    ).scalar_one()
    assert drift == 0


def test_some_tracked_usage_has_no_booking(db):
    """Spec 01: 'some tracked without booking' — the reconciliation story needs these."""
    orphans = db.execute(
        text(
            "SELECT count(*) FROM infinity.usage_records "
            "WHERE source = 'tracked' AND booking_id IS NULL"
        )
    ).scalar_one()
    assert orphans > 0


def test_every_service_request_has_samples(db):
    without = db.execute(
        text(
            """SELECT count(*) FROM infinity.service_requests sr
               WHERE NOT EXISTS (SELECT 1 FROM infinity.samples s WHERE s.request_id = sr.id)"""
        )
    ).scalar_one()
    assert without == 0


def test_demo_identities_present_with_expected_roles(db):
    rows = dict(
        db.execute(
            text(
                "SELECT id, role FROM infinity.users "
                "WHERE id IN ('u-alice','u-bob','u-asha','u-cora')"
            )
        ).all()
    )
    assert rows == {"u-alice": "user", "u-bob": "user", "u-asha": "pi", "u-cora": "admin"}
    assert db.execute(
        text("SELECT pi_user_id FROM infinity.labs WHERE id = 'lab-a'")
    ).scalar_one() == "u-asha"


def test_march_billing_story_is_exact(db):
    """The demo's verifiable number: Lab A, March 2026, Confocal C2 == $412.00."""
    total = db.execute(
        text(
            """SELECT sum(amount) FROM reporting.v_billing_lines
               WHERE lab_id = 'lab-a' AND period = '2026-03' AND instrument = 'Confocal C2'"""
        )
    ).scalar_one()
    assert float(total) == 412.00


# --- read-only role -------------------------------------------------------------


@pytest.mark.parametrize("view", ALLOW_LISTED_VIEWS)
def test_readonly_role_can_select_allow_listed_views(rodb, view):
    rodb.execute(text(f"SELECT * FROM {view} LIMIT 1"))


def test_readonly_role_cannot_insert(rodb):
    with pytest.raises((DBAPIError, ProgrammingError)):
        rodb.execute(
            text("INSERT INTO v_bookings (user_id) VALUES ('u-mallory')")
        )
    rodb.rollback()


def test_readonly_role_cannot_write_base_tables(rodb):
    for stmt in (
        "INSERT INTO infinity.users (id, email, name, role) "
        "VALUES ('u-mallory', 'm@x.io', 'M', 'admin')",
        "UPDATE infinity.invoice_lines SET amount = 0",
        "DELETE FROM infinity.bookings",
        "UPDATE echomind.actions SET status = 'approved'",
    ):
        with pytest.raises((DBAPIError, ProgrammingError)):
            rodb.execute(text(stmt))
        rodb.rollback()


def test_readonly_role_cannot_read_base_tables(rodb):
    """No USAGE on infinity/echomind: there is no privilege path to a base table at all."""
    for stmt in (
        "SELECT * FROM infinity.users LIMIT 1",
        "SELECT * FROM infinity.invoice_lines LIMIT 1",
        "SELECT * FROM echomind.chunks LIMIT 1",
        "SELECT * FROM echomind.actions LIMIT 1",
    ):
        with pytest.raises((DBAPIError, ProgrammingError)):
            rodb.execute(text(stmt))
        rodb.rollback()


def test_readonly_role_cannot_create_objects(rodb):
    with pytest.raises((DBAPIError, ProgrammingError)):
        rodb.execute(text("CREATE TABLE public.mallory (id int)"))
    rodb.rollback()


# --- app role (runtime) ---------------------------------------------------------


def test_app_role_can_do_its_job_but_owns_nothing(db):
    """`echomind_app` must be able to run the application and nothing more.

    Asserted against the role directly rather than through `engine`, so the guarantee
    holds even on a dev checkout where APP_DATABASE_URL is unset and the API still
    connects as the owner.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.exc import SQLAlchemyError

    from server.config import settings

    url = settings.readonly_database_url.replace(
        f"{settings.readonly_db_user}:{settings.readonly_db_password}",
        "echomind_app:echomind_app",
    ).replace("postgresql://", "postgresql+psycopg://", 1)
    app_engine = create_engine(url, pool_pre_ping=True, future=True)

    try:
        with app_engine.connect() as conn:
            # It can read the platform, the reporting views and its own state...
            conn.execute(text("SELECT count(*) FROM infinity.bookings"))
            conn.execute(text("SELECT count(*) FROM reporting.v_billing_lines"))
            conn.execute(text("SELECT count(*) FROM echomind.actions"))
            # ...including the checkpointer tables, wherever search_path puts them.
            conn.execute(text("SELECT count(*) FROM checkpoints"))

        # ...and it can write application state.
        with app_engine.begin() as conn:
            conn.execute(
                text("INSERT INTO echomind.audit_log (event, actor_id) "
                     "VALUES ('role_probe', 'test')")
            )
            conn.execute(text("DELETE FROM echomind.audit_log WHERE event = 'role_probe'"))

        # But it cannot reshape the database or write the platform's own tables.
        for statement in (
            "CREATE TABLE public.role_probe (id int)",
            "DROP TABLE infinity.bookings",
            "ALTER TABLE infinity.users ADD COLUMN role_probe int",
            "INSERT INTO infinity.users (id, email, name, role) "
            "VALUES ('u-probe', 'p@x.io', 'P', 'admin')",
        ):
            with pytest.raises(SQLAlchemyError):
                with app_engine.begin() as conn:
                    conn.execute(text(statement))
    finally:
        app_engine.dispose()


def test_readonly_session_is_read_only_and_time_limited(rodb):
    assert rodb.execute(text("SHOW default_transaction_read_only")).scalar_one() == "on"
    assert rodb.execute(text("SHOW statement_timeout")).scalar_one() in ("5s", "5000ms")
