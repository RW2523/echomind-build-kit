"""The knowledge library: what this assistant can read, and what is in it.

Every answer already names its sources, so the corpus was visible one citation at a time
and nowhere as a whole. A reader who wanted to know what the assistant had been given had
to guess questions until the documents surfaced. That is the wrong way round — the shelf
should be readable before you ask anything of it.

The queries themselves live in `server.rag.retrieval`, beside the retriever and under the
same permission predicate. This module only shapes the result for a reader: no SQL here,
which is what keeps the listing and the retrieval from growing separate ideas of who may
see what.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from server.auth import Ctx, require_ctx
from server.mcp.errors import ToolError
from server.rag import retrieval

from .actions import _http

log = logging.getLogger("echomind.api.library")

router = APIRouter(prefix="/library", tags=["library"])


def _scope_of(row) -> str:
    """How widely a document reaches, in the words a reader thinks in.

    "public" is the database's word for it; "everyone" is what it means to the person
    deciding whether a policy applies to them.
    """
    if row["visibility"] == "public":
        return "everyone"
    if row["visibility"] == "lab":
        return f"lab {row['lab_id']}" if row["lab_id"] else "lab"
    return "only you"


def _when(value) -> str | None:
    return value.isoformat() if value else None


@router.get("")
def list_documents(ctx: Ctx = Depends(require_ctx)) -> dict:
    """Every document this caller could be answered from."""
    documents = [
        {
            "doc_id": row["id"],
            "title": row["title"],
            "version": row["version"],
            "visibility": row["visibility"],
            "scope": _scope_of(row),
            "chunks": row["chunks"],
            "characters": int(row["characters"] or 0),
            "source_path": row["source_path"],
            "updated_at": _when(row["updated_at"]),
        }
        for row in retrieval.list_documents(ctx)
    ]
    return {
        "documents": documents,
        "total": len(documents),
        "chunks": sum(d["chunks"] for d in documents),
    }


@router.get("/{doc_id}")
def read_document(doc_id: str, ctx: Ctx = Depends(require_ctx)) -> dict:
    """The document itself, as the assistant has it indexed."""
    found = retrieval.read_document(ctx, doc_id)
    # Same indistinguishability rule as the tool layer: a caller who may not read this
    # cannot learn whether it exists.
    if found is None:
        raise _http(ToolError("not_found", "No such document.", ""))

    return {
        "doc_id": found["id"],
        "title": found["title"],
        "version": found["version"],
        "visibility": found["visibility"],
        "scope": _scope_of(found),
        "source_path": found["source_path"],
        "updated_at": _when(found["updated_at"]),
        "sections": [
            {"ord": s["ord"], "breadcrumb": s["breadcrumb"], "text": s["text"]}
            for s in found["sections"]
        ],
    }
