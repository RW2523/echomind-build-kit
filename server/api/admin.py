"""Admin API — audit trail, traces and the latest eval scores.

Admin-only, enforced here rather than in the UI: spec 07 requires both a route guard and
an API guard, and only one of those is real.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from server.auth import Ctx, require_ctx
from server.config import settings
from server.db import session_scope
from server.mcp import actions as actions_mod
from server.observability import tracer

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(ctx: Ctx = Depends(require_ctx)) -> Ctx:
    if not ctx.is_admin:
        # 404, not 403: an admin surface should not confirm its own existence.
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "Not found.", "hint": ""}
        )
    return ctx


@router.get("/audit")
def audit(
    ctx: Ctx = Depends(require_admin),
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    clauses, params = [], {"limit": limit}
    if status:
        clauses.append("a.status = :status")
        params["status"] = status
    if kind:
        clauses.append("a.tool = :tool")
        params["tool"] = _tool_for_kind(kind)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with session_scope() as s:
        actions = s.execute(
            text(
                f"""SELECT a.id, a.user_id, a.tool, a.payload, a.status, a.approver_id,
                           a.created_at, a.decided_at, a.executed_at, a.result, a.thread_id
                    FROM echomind.actions a {where}
                    ORDER BY a.created_at DESC LIMIT :limit"""
            ),
            params,
        ).mappings().all()
        events = s.execute(
            text(
                """SELECT action_id, event, actor_id, tool, detail, created_at
                   FROM echomind.audit_log ORDER BY created_at DESC, id DESC LIMIT :limit"""
            ),
            {"limit": limit},
        ).mappings().all()

    return {
        "actions": [dict(a) for a in actions],
        "events": [dict(e) for e in events],
        "kinds": sorted(set(actions_mod.ACTION_KIND.values())),
        "statuses": ["pending", "approved", "declined", "executed", "failed"],
    }


def _tool_for_kind(kind: str) -> str:
    for tool, action_kind in actions_mod.ACTION_KIND.items():
        if action_kind == kind:
            return tool
    return kind


@router.get("/evals")
def latest_evals(ctx: Ctx = Depends(require_admin),
                 limit: int = Query(default=5, ge=1, le=50)) -> dict:
    with session_scope() as s:
        rows = s.execute(
            text(
                "SELECT id, ran_at, metrics FROM echomind.eval_runs "
                "ORDER BY ran_at DESC LIMIT :limit"
            ),
            {"limit": limit},
        ).mappings().all()
    runs = [dict(r) for r in rows]
    return {"latest": runs[0] if runs else None, "runs": runs}


@router.get("/traces")
def traces(ctx: Ctx = Depends(require_admin), limit: int = Query(default=50, ge=1, le=500)) -> dict:
    """Whichever sink is live — Langfuse when enabled, traces.jsonl otherwise."""
    return {
        "sink": tracer.sink,
        "langfuse_enabled": settings.langfuse_enabled,
        "langfuse_url": settings.langfuse_host if settings.langfuse_enabled else None,
        "spans": tracer.recent(limit),
    }


@router.get("/summary")
def summary(ctx: Ctx = Depends(require_admin)) -> dict:
    with session_scope() as s:
        counts = dict(
            s.execute(
                text("SELECT status, count(*) FROM echomind.actions GROUP BY status")
            ).all()
        )
        docs = s.execute(
            text("SELECT count(*) FROM echomind.knowledge_docs")
        ).scalar_one()
        latest_eval = s.execute(
            text("SELECT metrics FROM echomind.eval_runs ORDER BY ran_at DESC LIMIT 1")
        ).scalar_one_or_none()
    return {
        "actions_by_status": counts,
        "knowledge_docs": docs,
        "latest_eval": latest_eval,
        "trace_sink": tracer.sink,
        "escalation_enabled": settings.escalation_enabled,
        "model": settings.llm_model,
        "reranker": settings.reranker,
    }


@router.get("/gaps")
def knowledge_gaps(ctx: Ctx = Depends(require_admin),
                   limit: int = Query(default=20, ge=1, le=100),
                   days: int = Query(default=90, ge=1, le=365)) -> dict:
    """What the assistant was asked and could not answer, ranked.

    The refusals are the content roadmap: each row is a document the facility has not
    written, in the words the people who wanted it actually used. Ranked by distinct
    askers first, so one person asking twelve times does not outrank six people asking
    once.
    """
    from server.agent import gaps

    rows = gaps.ranked(limit=limit, days=days)
    return {"window_days": days, "gaps": rows, "count": len(rows)}


@router.get("/prompts")
def prompt_versions(ctx: Ctx = Depends(require_admin)) -> dict:
    """Which prompt produced what, six weeks later.

    The version is a hash of the prompt text, so it cannot drift from what actually ran —
    a hand-bumped number is wrong exactly when it matters, after someone tweaked a
    sentence to fix one eval case and forgot.
    """
    from server.agent.prompts import ensure_registered

    versions = ensure_registered()
    return {"prompts": versions, "count": len(versions)}
