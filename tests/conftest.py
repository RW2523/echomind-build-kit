"""Shared fixtures.

The suite runs against the live seeded dev database — the security properties under test
(read-only role, view allow-list, permission-filtered retrieval) are properties of the
real Postgres grants, so mocking them would test nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from server.auth import mint
from server.db import engine, owner_engine, ro_engine
from server.demo_identities import DEMO_USERS


def _database_ready() -> bool:
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT count(*) FROM infinity.users")
            ).scalar_one() > 0
    except Exception:
        return False


DB_READY = _database_ready()

requires_db = pytest.mark.skipif(
    not DB_READY,
    reason="seeded database unavailable — run `make up && make seed`",
)


def pytest_collection_modifyitems(config, items):
    """Every test here needs the seeded database; skip cleanly rather than erroring."""
    if DB_READY:
        return
    skip = pytest.mark.skip(reason="seeded database unavailable — run `make up && make seed`")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(autouse=True)
def restore_seeded_state():
    """Undo whatever a test wrote, so the suite is re-runnable and order-independent.

    Approved actions really do mutate infinity.*, which would otherwise break the seeded
    row counts on the next run. Each executed action records exactly what it created, so
    cleanup is precise rather than a blunt re-seed.
    """
    if not DB_READY:
        yield
        return

    # action ids are random hex, so "created during this test" has to be a set difference
    # rather than an ordering comparison.
    with engine.connect() as conn:
        before = set(conn.execute(text("SELECT id FROM echomind.actions")).scalars().all())
    yield

    # As the owner: echomind_app may create a booking on approval and is deliberately
    # not allowed to delete one, so the tear-down cannot borrow the application's role.
    with owner_engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, result FROM echomind.actions WHERE id <> ALL(:before)"),
            {"before": list(before)},
        ).mappings().all()

        for row in rows:
            result = row["result"] or {}
            created = result.get("created")
            if created == "booking":
                conn.execute(
                    text("DELETE FROM infinity.bookings WHERE id = :id"),
                    {"id": result["booking_id"]},
                )
            elif created == "service_request":
                conn.execute(
                    text("DELETE FROM infinity.samples WHERE request_id = :id"),
                    {"id": result["request_id"]},
                )
                conn.execute(
                    text("DELETE FROM infinity.service_requests WHERE id = :id"),
                    {"id": result["request_id"]},
                )
            elif created == "user":
                conn.execute(
                    text("DELETE FROM infinity.users WHERE id = :id"),
                    {"id": result["user_id"]},
                )
            elif created == "document":
                Path(result["absolute_path"]).unlink(missing_ok=True)

        # audit_log rows cascade from actions.
        conn.execute(
            text("DELETE FROM echomind.actions WHERE id <> ALL(:before)"),
            {"before": list(before)},
        )


@pytest.fixture(scope="session")
def db():
    with engine.connect() as conn:
        yield conn


@pytest.fixture(scope="session")
def rodb():
    """Connection as echomind_readonly — the role agent SQL runs under."""
    with ro_engine.connect() as conn:
        yield conn


@pytest.fixture(scope="session")
def tokens() -> dict[str, str]:
    return {
        handle: mint(
            user_id=u["id"],
            name=u["name"],
            role=u["role"],
            lab_ids=u["lab_ids"],
            facility_ids=u["facility_ids"],
        )
        for handle, u in DEMO_USERS.items()
    }


@pytest.fixture(scope="session")
def ctxs() -> dict:
    from server.auth import Ctx

    return {
        handle: Ctx(
            user_id=u["id"],
            name=u["name"],
            role=u["role"],
            lab_ids=tuple(u["lab_ids"]),
            facility_ids=tuple(u["facility_ids"]),
        )
        for handle, u in DEMO_USERS.items()
    }
