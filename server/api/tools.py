"""Direct HTTP access to the tool layer.

The same handlers the MCP server and the agent use — exposed over HTTP so the demo
script and the UI can call a tool without speaking MCP. Enforcement is identical
because it lives in the handler, not in the transport.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from server.auth import Ctx, require_ctx
from server.mcp import tools as tools_mod
from server.mcp.errors import ToolError

router = APIRouter(prefix="/tools", tags=["tools"])

STATUS_FOR_CODE = {"forbidden": 403, "not_found": 404, "conflict": 409, "invalid_params": 400,
                   "sql_rejected": 400, "unauthenticated": 401}


@router.get("")
def list_tools(ctx: Ctx = Depends(require_ctx)) -> dict:
    return {
        "tools": [
            {
                "number": spec.number,
                "name": spec.name,
                "tier": spec.tier,
                "write": spec.write,
                "description": spec.description,
                "params": spec.params,
            }
            for spec in sorted(tools_mod.TOOLS.values(), key=lambda s: s.number)
        ]
    }


@router.get("/chunk/{chunk_id}")
def get_chunk(chunk_id: int, ctx: Ctx = Depends(require_ctx)) -> dict:
    """Chunk text behind a citation chip.

    The chunk id comes from the client, so the permission predicate is applied again
    here — a citation chip must not become a way to read chunks by guessing ids.
    """
    from server.rag.retrieval import chunk_for_citation

    chunk = chunk_for_citation(chunk_id, ctx)
    if chunk is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "No such source.", "hint": ""},
        )
    return chunk


@router.post("/{name}")
def call_tool(
    name: str,
    arguments: dict[str, Any] = Body(default_factory=dict),
    ctx: Ctx = Depends(require_ctx),
) -> dict:
    try:
        return {"tool": name, "result": tools_mod.call(ctx, name, arguments)}
    except ToolError as exc:
        raise HTTPException(
            status_code=STATUS_FOR_CODE.get(exc.code, 400),
            detail=exc.to_dict()["error"],
        ) from exc
    except TypeError as exc:  # bad/unknown argument names
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_params", "message": str(exc), "hint": "Check the argument names."},
        ) from exc
