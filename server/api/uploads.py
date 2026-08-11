"""Personal RAG uploads.

Spec 03: UI uploads call the same ingestion pipeline with visibility='private' and
owner_user_id=caller. There is no way through this endpoint to create a document that
anyone else can retrieve.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from server.auth import Ctx, require_ctx
from server.rag.ingest import delete_doc, ingest_file, list_user_docs

log = logging.getLogger("echomind.uploads")

router = APIRouter(prefix="/uploads", tags=["uploads"])

ALLOWED_SUFFIXES = {".md", ".markdown", ".pdf", ".txt"}
MAX_BYTES = 5 * 1024 * 1024


@router.get("")
def list_uploads(ctx: Ctx = Depends(require_ctx)) -> dict:
    return {"uploads": list_user_docs(ctx.user_id)}


@router.post("")
async def upload(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    ctx: Ctx = Depends(require_ctx),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_params",
                "message": f"Unsupported file type '{suffix or 'unknown'}'.",
                "hint": "Upload a .md, .txt or .pdf file.",
            },
        )

    body = await file.read()
    if len(body) > MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_params",
                    "message": "File is larger than 5 MB.", "hint": "Split it up."},
        )

    with tempfile.TemporaryDirectory() as tmp:
        # .txt goes through the markdown path; it is just unstructured prose.
        staged = Path(tmp) / (Path(file.filename).stem + (".md" if suffix == ".txt" else suffix))
        staged.write_bytes(body)
        result = ingest_file(
            staged,
            visibility="private",
            owner_user_id=ctx.user_id,
            title=title or Path(file.filename).stem.replace("-", " ").replace("_", " ").title(),
        )

    log.info("upload by %s -> %s (%d chunks)", ctx.user_id, result["doc_id"], result["chunks"])
    return {
        **result,
        "owner_user_id": ctx.user_id,
        "note": ("Only you can see this. It is private to your account and never "
                 "retrieved for anyone else."),
    }


@router.delete("/{doc_id}")
def delete_upload(doc_id: str, ctx: Ctx = Depends(require_ctx)) -> dict:
    """Hard delete — document and chunks (spec 03). Scoped to the caller's own uploads."""
    deleted = delete_doc(doc_id, owner_user_id=ctx.user_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "No such upload.", "hint": ""},
        )
    return {"deleted": doc_id}
