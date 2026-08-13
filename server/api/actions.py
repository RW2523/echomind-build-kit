"""Approval API. The only path by which a pending action becomes a real mutation."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from server.auth import Ctx, require_ctx
from server.config import REPO_ROOT
from server.mcp import actions as actions_mod
from server.mcp.errors import ToolError

log = logging.getLogger("echomind.api.actions")

router = APIRouter(prefix="/actions", tags=["actions"])

STATUS_FOR_CODE = {"forbidden": 403, "not_found": 404, "conflict": 409, "invalid_params": 400}


def _http(exc: ToolError) -> HTTPException:
    return HTTPException(status_code=STATUS_FOR_CODE.get(exc.code, 400),
                         detail=exc.to_dict()["error"])


@router.get("")
def list_actions(ctx: Ctx = Depends(require_ctx), limit: int = Query(50, ge=1, le=200)) -> dict:
    return {"actions": actions_mod.list_actions(ctx, limit=limit)}


@router.get("/{action_id}")
def get_action(action_id: str, ctx: Ctx = Depends(require_ctx)) -> dict:
    action = actions_mod.get_action(action_id)
    # Same indistinguishability rule as the tool layer: a caller who may not see the
    # action cannot learn whether it exists.
    if action is None or not (ctx.is_admin or action["user_id"] == ctx.user_id):
        raise _http(ToolError("not_found", "No such action.", ""))
    return {"action": action, "audit": actions_mod.audit_trail(ctx, action_id=action_id)}


_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown; charset=utf-8",
}


@router.get("/{action_id}/document")
def download_document(action_id: str, ctx: Ctx = Depends(require_ctx)) -> FileResponse:
    """Hand back the file an approved generate_document actually produced.

    Until this existed the platform wrote a document to disk and told the user a
    filesystem path their browser could not reach — a generated document nobody can
    retrieve is half a feature, and every one produced so far had to be fetched off the
    box by hand.

    Entitlement is the same rule as everywhere else and for the same reason: a caller who
    may not see the action cannot learn whether it exists, so the wrong owner and a
    made-up id give byte-identical answers. The path is taken from the action's own
    recorded result and re-resolved under the outputs directory, so a stored value that
    somehow pointed elsewhere cannot serve an arbitrary file.
    """
    action = actions_mod.get_action(action_id)
    if action is None or not (ctx.is_admin or action["user_id"] == ctx.user_id):
        raise _http(ToolError("not_found", "No such action.", ""))

    result = action.get("result") or {}
    stored = result.get("path")
    produced_a_file = (action.get("tool") == "generate_document"
                       and action.get("status") == "executed" and stored)
    if not produced_a_file:
        raise _http(ToolError(
            "not_found", "That action did not produce a document.",
            "Only an approved document generation has a file to download.",
        ))

    outputs = (REPO_ROOT / "files" / "outputs").resolve()
    path = (REPO_ROOT / stored).resolve()
    if not path.is_file() or outputs not in path.parents:
        raise _http(ToolError(
            "not_found", "The generated file is no longer on disk.",
            "Ask for the document again and it will be regenerated.",
        ))

    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        filename=path.name,
    )


def _resume_conversation(action_id: str) -> dict | None:
    """If the action came from a chat turn, resume that thread so the confirmation lands
    in the conversation (spec 04 §4). A failure to resume must not undo the execution."""
    action = actions_mod.get_action(action_id)
    if not action or not action.get("thread_id"):
        return None
    try:
        from server.agent.graph import resume_turn

        response = resume_turn(action["thread_id"], action)
        return response.to_dict() if response else None
    except Exception:
        log.exception("could not resume thread for action %s", action_id)
        return None


@router.post("/{action_id}/approve")
def approve(action_id: str, ctx: Ctx = Depends(require_ctx)) -> dict:
    try:
        outcome = actions_mod.approve(ctx, action_id)
    except ToolError as exc:
        raise _http(exc) from exc
    chat = _resume_conversation(action_id)
    return {**outcome, "chat": chat} if chat else outcome


@router.post("/{action_id}/decline")
def decline(action_id: str, ctx: Ctx = Depends(require_ctx)) -> dict:
    try:
        outcome = actions_mod.decline(ctx, action_id)
    except ToolError as exc:
        raise _http(exc) from exc
    chat = _resume_conversation(action_id)
    return {**outcome, "chat": chat} if chat else outcome
