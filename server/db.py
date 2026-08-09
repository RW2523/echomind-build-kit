"""Database engines.

Three roles, deliberately, in decreasing order of privilege:

  * `owner_engine` — schema owner. Migrations, seeding, checkpointer DDL. Nothing that
                     serves a request touches it.
  * `engine`       — `echomind_app`: CRUD on the echomind schema, read on infinity and
                     reporting, NO DDL. This is what the running API uses, so a bug in
                     application code cannot alter the schema or drop a table.
  * `ro_engine`    — `echomind_readonly`: SELECT on the four reporting views and nothing
                     else. EVERY piece of agent-generated SQL runs here, so a validator
                     bug cannot mutate data. Golden rule 3.

On a clean dev checkout APP_DATABASE_URL is unset and `engine` falls back to the owner,
so nothing has to be provisioned before `make seed` works.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from server.config import settings


def _sqlalchemy_url(url: str) -> str:
    # psycopg 3 driver; accept plain postgresql:// in .env for familiarity.
    if url.startswith("postgresql+"):
        return url
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


owner_engine: Engine = create_engine(
    _sqlalchemy_url(settings.database_url),
    pool_pre_ping=True,
    future=True,
)

engine: Engine = create_engine(
    _sqlalchemy_url(settings.runtime_database_url),
    pool_pre_ping=True,
    future=True,
)

ro_engine: Engine = create_engine(
    _sqlalchemy_url(settings.readonly_database_url),
    pool_pre_ping=True,
    future=True,
    # Belt and braces: the role is already read-only and search_path-pinned in Postgres
    # (003_roles.sql); the connection asserts the same thing so a mis-provisioned
    # database fails closed rather than open.
    connect_args={
        "options": (
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={settings.sql_timeout_ms} "
            "-c search_path=reporting"
        )
    },
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
OwnerSessionLocal = sessionmaker(bind=owner_engine, expire_on_commit=False, future=True)
ROSessionLocal = sessionmaker(bind=ro_engine, expire_on_commit=False, future=True)


@contextmanager
def owner_session() -> Iterator[Session]:
    """Owner-role session. Migrations and seeding only — never a request path."""
    s = OwnerSessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Owner-role session with commit/rollback handling."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@contextmanager
def ro_session() -> Iterator[Session]:
    """Read-only session. Never commits."""
    s = ROSessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s


def ping() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
