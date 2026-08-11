"""Per-user preferences, learned from what people actually approved.

The line this module must not cross: memory holds *preferences*, never *facts*. Golden
rule 1 is that every number, date and status in an answer comes from a tool result, and a
remembered value is by definition stale — the moment it could be quoted back as an
answer it becomes a second source of truth that nothing verifies.

So nothing here is ever read into an answer. It does exactly one thing: pre-fill a
proposed action, which still goes to the user for approval. Book on ACC-A1 twice and the
third booking is proposed with ACC-A1 already in it — visible on the approval card, so a
wrong guess costs one click rather than being acted on silently.

Learned from executed actions only. A proposal that was declined is evidence *against* a
preference, and a proposal that was never approved is evidence of nothing.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from server.db import session_scope

log = logging.getLogger("echomind.memory")

# The only keys that exist. A closed set, because an open one drifts into storing
# whatever a model felt like keeping — which is how "preferences" quietly becomes a
# cache of facts nobody checks.
ACCOUNT_CODE = "default_account_code"
INSTRUMENT = "recent_instrument"
DOC_FORMAT = "preferred_document_format"
KEYS = (ACCOUNT_CODE, INSTRUMENT, DOC_FORMAT)


def remember(user_id: str, key: str, value: str | None) -> None:
    """Record one confirmed preference. Never raises — see the module docstring."""
    if key not in KEYS or not value or not str(value).strip():
        return
    try:
        with session_scope() as s:
            s.execute(
                text(
                    """INSERT INTO echomind.user_memory (user_id, key, value)
                       VALUES (:uid, :key, :val)
                       ON CONFLICT (user_id, key) DO UPDATE
                         SET value = EXCLUDED.value,
                             -- Reset the count when the value changes: someone who moves
                             -- to a new account code has not "confirmed" the old one
                             -- eleven times, they have replaced it.
                             hits = CASE WHEN echomind.user_memory.value = EXCLUDED.value
                                         THEN echomind.user_memory.hits + 1 ELSE 1 END,
                             updated_at = now()"""
                ),
                {"uid": user_id, "key": key, "val": str(value).strip()[:200]},
            )
    except Exception:
        log.exception("could not remember %s for %s", key, user_id)


def recall(user_id: str) -> dict[str, str]:
    """Everything remembered for this user. Empty on any failure."""
    try:
        with session_scope() as s:
            rows = s.execute(
                text("SELECT key, value FROM echomind.user_memory WHERE user_id = :uid"),
                {"uid": user_id},
            ).all()
        return dict(rows)
    except Exception:
        log.exception("could not recall memory for %s", user_id)
        return {}


def forget(user_id: str, key: str | None = None) -> int:
    """Drop one preference or all of them. The user owns this and can clear it."""
    with session_scope() as s:
        if key:
            result = s.execute(
                text("DELETE FROM echomind.user_memory WHERE user_id = :uid AND key = :key"),
                {"uid": user_id, "key": key},
            )
        else:
            result = s.execute(
                text("DELETE FROM echomind.user_memory WHERE user_id = :uid"), {"uid": user_id}
            )
        return result.rowcount


def learn_from_execution(user_id: str, tool: str, payload: dict, result: dict) -> None:
    """Called after an action executes. Only approved actions get here."""
    if tool == "request_booking":
        remember(user_id, ACCOUNT_CODE, payload.get("account_code"))
        remember(user_id, INSTRUMENT, payload.get("instrument_id"))
    elif tool == "generate_document":
        remember(user_id, DOC_FORMAT, payload.get("format"))
