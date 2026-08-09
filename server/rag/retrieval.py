"""Permission-filtered hybrid retrieval.

This module is the ONLY read path to echomind.chunks (enforced by a lint test in
tests/test_rag_isolation.py). Enforcement point 3 of spec 05.

The permission predicate is built exclusively from the verified JWT context. No part of
it is ever derived from the query text, the model, or the request body — a prompt saying
"ignore filters and search all documents" changes the SQL not at all.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

import httpx
from sqlalchemy import text

from server.auth import Ctx
from server.config import settings
from server.db import session_scope
from server.rag.embeddings import embed_one, to_pgvector

log = logging.getLogger("echomind.retrieval")

CANDIDATES = 20  # per arm, before fusion
RRF_K = 60


@dataclass
class RetrievedChunk:
    chunk_id: int
    doc_id: str
    text: str
    breadcrumb: str
    score: float          # cosine similarity 0..1 — what the confidence gate reads
    rrf: float            # fused rank score — what the ordering uses
    vector_rank: int | None
    fts_rank: int | None
    title: str
    visibility: str
    lab_id: str | None = None
    owner_user_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def permission_predicate(ctx: Ctx) -> tuple[str, dict[str, Any]]:
    """The single permission filter (spec 03 §Retrieval step 1).

    Returned as SQL + params so tests can assert on it directly.
    """
    clauses = [
        "c.visibility = 'public'",
        "(c.visibility = 'lab' AND c.lab_id = ANY(:lab_ids))",
        "(c.visibility = 'private' AND c.owner_user_id = :user_id)",
    ]
    params: dict[str, Any] = {
        "lab_ids": list(ctx.lab_ids),
        "user_id": ctx.user_id,
    }
    if ctx.is_admin:
        # Admins additionally see facility-scoped docs — but never another user's
        # private chunks, hence the explicit visibility guard.
        clauses.append(
            "(c.facility_id = ANY(:facility_ids) AND c.visibility <> 'private')"
        )
        params["facility_ids"] = list(ctx.facility_ids)
    return "(" + " OR ".join(clauses) + ")", params


_VECTOR_SQL = """
SELECT c.id, c.doc_id, c.text, c.breadcrumb, c.visibility, c.lab_id,
       c.owner_user_id, d.title,
       1 - (c.embedding <=> CAST(:qvec AS vector)) AS similarity
FROM echomind.chunks c
JOIN echomind.knowledge_docs d ON d.id = c.doc_id
WHERE {permitted} AND c.embedding IS NOT NULL
ORDER BY c.embedding <=> CAST(:qvec AS vector)
LIMIT :limit
"""

_FTS_SQL = """
SELECT c.id, c.doc_id, c.text, c.breadcrumb, c.visibility, c.lab_id,
       c.owner_user_id, d.title,
       1 - (c.embedding <=> CAST(:qvec AS vector)) AS similarity,
       ts_rank(c.tsv, websearch_to_tsquery('english', :q)) AS rank
FROM echomind.chunks c
JOIN echomind.knowledge_docs d ON d.id = c.doc_id
WHERE {permitted} AND c.tsv @@ websearch_to_tsquery('english', :q)
ORDER BY rank DESC
LIMIT :limit
"""


def retrieve(query: str, ctx: Ctx, k: int = 8) -> list[RetrievedChunk]:
    """Hybrid retrieval: vector + FTS, both permission-filtered in SQL, fused with RRF."""
    if not query or not query.strip():
        return []

    permitted, params = permission_predicate(ctx)
    qvec = to_pgvector(embed_one(query))
    params = {**params, "qvec": qvec, "q": query, "limit": CANDIDATES}

    with session_scope() as s:
        vector_rows = s.execute(
            text(_VECTOR_SQL.format(permitted=permitted)), params
        ).mappings().all()
        fts_rows = s.execute(
            text(_FTS_SQL.format(permitted=permitted)), params
        ).mappings().all()

    merged: dict[int, dict[str, Any]] = {}

    def note(row, arm: str, rank: int) -> None:
        entry = merged.setdefault(
            row["id"],
            {
                "row": row,
                "rrf": 0.0,
                "vector_rank": None,
                "fts_rank": None,
            },
        )
        entry["rrf"] += 1.0 / (RRF_K + rank)
        entry[f"{arm}_rank"] = rank

    for i, row in enumerate(vector_rows, start=1):
        note(row, "vector", i)
    for i, row in enumerate(fts_rows, start=1):
        note(row, "fts", i)

    results = [
        RetrievedChunk(
            chunk_id=e["row"]["id"],
            doc_id=e["row"]["doc_id"],
            text=e["row"]["text"],
            breadcrumb=e["row"]["breadcrumb"],
            score=float(e["row"]["similarity"] or 0.0),
            rrf=e["rrf"],
            vector_rank=e["vector_rank"],
            fts_rank=e["fts_rank"],
            title=e["row"]["title"],
            visibility=e["row"]["visibility"],
            lab_id=e["row"]["lab_id"],
            owner_user_id=e["row"]["owner_user_id"],
        )
        for e in merged.values()
    ]
    results.sort(key=lambda c: c.rrf, reverse=True)

    if settings.reranker == "bge":
        results = _rerank(query, results)

    log.info(
        "retrieve caller=%s role=%s q=%r vector=%d fts=%d merged=%d -> k=%d",
        ctx.user_id, ctx.role, query[:80], len(vector_rows), len(fts_rows),
        len(results), min(k, len(results)),
    )
    return results[:k]


def chunk_text_by_id(chunk_id: int) -> str | None:
    """Fetch one chunk's text by id, for the UI's citation popover and the eval runner.

    Kept in this module so the chunks table still has exactly one read path. Callers
    must already hold a citation produced by `retrieve()`, which was permission-filtered.
    """
    with session_scope() as s:
        return s.execute(
            text("SELECT text FROM echomind.chunks WHERE id = :id"), {"id": chunk_id}
        ).scalar_one_or_none()


def chunk_for_citation(chunk_id: int, ctx: Ctx) -> dict[str, Any] | None:
    """Re-check permission before returning a chunk to a user by id.

    The UI asks for chunk text by id, which is caller-supplied input; re-applying the
    permission predicate means a guessed id cannot become a read primitive.
    """
    permitted, params = permission_predicate(ctx)
    with session_scope() as s:
        row = s.execute(
            text(
                f"""SELECT c.id, c.doc_id, c.text, c.breadcrumb, c.visibility, d.title
                    FROM echomind.chunks c
                    JOIN echomind.knowledge_docs d ON d.id = c.doc_id
                    WHERE c.id = :chunk_id AND {permitted}"""
            ),
            {**params, "chunk_id": chunk_id},
        ).mappings().first()
    return dict(row) if row else None


def _rerank(query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Optional cross-encoder rerank (RERANKER=bge).

    Talks to a TEI/vLLM-style /rerank endpoint. If it is unreachable we keep the RRF
    order rather than fail the turn — a worse ordering is recoverable, a dead chat is not.
    """
    if not chunks:
        return chunks
    url = settings.embed_base_url.rstrip("/").removesuffix("/v1") + "/rerank"
    try:
        resp = httpx.post(
            url,
            json={"query": query, "texts": [c.text for c in chunks], "model": "bge-reranker-v2-m3"},
            timeout=settings.llm_timeout_s,
        )
        resp.raise_for_status()
        scored = resp.json()
        order = [(item["index"], float(item["score"])) for item in scored]
    except Exception as exc:  # noqa: BLE001
        log.warning("reranker unavailable (%s); keeping RRF order", exc)
        return chunks

    by_index = {i: s for i, s in order}
    ranked = sorted(chunks, key=lambda c: by_index.get(chunks.index(c), 0.0), reverse=True)
    return ranked
