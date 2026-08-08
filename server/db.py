"""Database engines.

Two roles, deliberately:
  * `engine`    — owner role. Migrations, seeding, writes to app tables (actions, audit,
                  chunks, checkpoints).
  * `ro_engine` — read-only role. EVERY piece of agent-generated SQL runs here, so a
                  validator bug cannot mutate data. Golden rule 3.
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


engine: Engine = create_engine(
    _sqlalchemy_url(settings.database_url),
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
ROSessionLocal = sessionmaker(bind=ro_engine, expire_on_commit=False, future=True)


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
