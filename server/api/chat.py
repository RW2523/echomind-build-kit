"""Chat endpoints: one JSON turn, and the SSE stream the UI uses."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import anyio.to_thread
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from server.agent.graph import get_graph, run_turn
from server.auth import Ctx, require_ctx
from server.db import session_scope

log = logging.getLogger("echomind.chat")

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = None


def _new_thread_id() -> str:
    return f"thr-{uuid.uuid4().hex[:12]}"


def _turn(message: str, ctx: Ctx, thread_id: str) -> dict[str, Any]:
    # run_turn opens the per-turn trace itself, so every entry point is traced alike.
    response = run_turn(message, ctx, thread_id)
    payload = response.to_dict()
    payload["thread_id"] = thread_id
    return payload


@router.post("/chat")
def chat(req: ChatRequest, ctx: Ctx = Depends(require_ctx)) -> dict:
    thread_id = req.thread_id or _new_thread_id()
    return _turn(req.message, ctx, thread_id)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, ctx: Ctx = Depends(require_ctx)) -> StreamingResponse:
    """SSE. The graph is not token-streaming, so we stream stage events and then the
    finished payload — the UI gets progressive feedback without the answer ever being
    shown before the gate and faithfulness checks have run on it."""
    thread_id = req.thread_id or _new_thread_id()

    async def events():
        yield _sse("start", {"thread_id": thread_id})
        task = asyncio.create_task(anyio.to_thread.run_sync(_turn, req.message, ctx, thread_id))
        stage = 0
        stages = [
            "understanding the question",
            "checking what you have access to",
            "verifying against sources",
        ]
        while not task.done():
            await asyncio.sleep(0.4)
            if stage < len(stages):
                yield _sse("status", {"stage": stages[stage]})
                stage += 1
        try:
            payload = task.result()
        except Exception as exc:
            log.exception("chat turn failed")
            yield _sse("error", {"message": str(exc)})
            return
        # Stream the text in chunks so it renders progressively.
        for piece in _pieces(payload.get("text", "")):
            yield _sse("token", {"text": piece})
            await asyncio.sleep(0.01)
        yield _sse("final", payload)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _pieces(text: str, size: int = 24) -> list[str]:
    words, out, buf = text.split(" "), [], ""
    for word in words:
        buf = f"{buf} {word}".strip()
        if len(buf) >= size:
            out.append(buf + " ")
            buf = ""
    if buf:
        out.append(buf)
    return out


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/threads/{thread_id}")
def get_thread(thread_id: str, ctx: Ctx = Depends(require_ctx)) -> dict:
    """Restore a conversation from the checkpointer (spec 07: refresh restores state)."""
    snapshot = get_graph().get_state({"configurable": {"thread_id": thread_id}})
    values = snapshot.values or {}
    owner = (values.get("ctx") or {}).get("user_id")
    if owner and owner != ctx.user_id and not ctx.is_admin:
        raise HTTPException(status_code=404, detail={"code": "not_found",
                                                     "message": "No such thread.", "hint": ""})
    return {
        "thread_id": thread_id,
        "message": values.get("message"),
        "route": values.get("route"),
        "response": values.get("response"),
        "awaiting_approval": bool(snapshot.next),
    }


@router.get("/conversations")
def list_conversations(ctx: Ctx = Depends(require_ctx), limit: int = 25) -> dict:
    """Threads this caller has proposed actions on — enough for the demo's left rail."""
    with session_scope() as s:
        rows = s.execute(
            text(
                """SELECT DISTINCT ON (thread_id) thread_id, created_at, tool, status
                   FROM echomind.actions
                   WHERE thread_id IS NOT NULL AND (:admin OR user_id = :uid)
                   ORDER BY thread_id, created_at DESC
                   LIMIT :limit"""
            ),
            {"uid": ctx.user_id, "admin": ctx.is_admin, "limit": limit},
        ).mappings().all()
    return {"conversations": [dict(r) for r in rows]}


@router.post("/chat/reset")
def reset(_: dict = Body(default_factory=dict), ctx: Ctx = Depends(require_ctx)) -> dict:
    """Switching user starts a fresh thread (spec 07)."""
    return {"thread_id": _new_thread_id()}
