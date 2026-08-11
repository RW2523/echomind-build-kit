"""What the assistant remembers about you, and how to clear it.

Preferences only — see server/agent/memory.py for why nothing here is ever quoted back
as an answer. Exposed because a user who cannot see what is held about them, or delete
it, does not really own it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from server.agent import memory
from server.auth import Ctx, require_ctx

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/memory")
def read_memory(ctx: Ctx = Depends(require_ctx)) -> dict:
    """Only ever your own: the key is the caller's verified id, never a parameter."""
    return {"user_id": ctx.user_id, "memory": memory.recall(ctx.user_id)}


@router.delete("/memory")
def clear_memory(ctx: Ctx = Depends(require_ctx), key: str | None = None) -> dict:
    removed = memory.forget(ctx.user_id, key)
    return {"removed": removed, "memory": memory.recall(ctx.user_id)}
