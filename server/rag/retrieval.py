"""Permission-filtered hybrid retrieval.

This module is the ONLY read path to echomind.chunks (enforced by a lint test in
tests/test_rag_isolation.py). Enforcement point 3 of spec 05.

The permission predicate is built exclusively from the verified JWT context. No part of
it is ever derived from the query text, the model, or the request body — a prompt saying
"ignore filters and search all documents" changes the SQL not at all.
"""

from __future__ import annotations

import hashlib
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
    # Cross-encoder score, when RERANKER=bge. Recorded rather than folded into `score`
    # because the two are on different scales — cosine sits in 0..1 and the gate's
    # threshold is calibrated for it, while these are unnormalised logits. Without this
    # the ordering came from one signal and the reported score from another, so a trace
    # could show the top chunk scoring below the chunk beneath it.
    rerank_score: float | None = None

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

    deduped = _dedupe(results)

    if settings.reranker_enabled:
        deduped = _rerank(query, deduped)
        deduped = _drop_far_tail(deduped, k)

    log.info(
        "retrieve caller=%s role=%s q=%r vector=%d fts=%d merged=%d deduped=%d -> k=%d",
        ctx.user_id, ctx.role, query[:80], len(vector_rows), len(fts_rows),
        len(results), len(deduped), min(k, len(deduped)),
    )
    return deduped[:k]


def _text_key(text: str) -> str:
    return hashlib.md5(" ".join(text.split()).lower().encode()).hexdigest()


def _dedupe(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Collapse chunks whose text is identical, keeping the best-ranked copy.

    The corpus instantiates templates under different titles, so 22 of 134 chunks are
    byte-identical to another — three "FAQ — Scheduling" documents carry the same words.
    Returning all three spends three of eight context slots on one piece of information,
    and pushes genuinely different material out of the window. Real facility corpora
    repeat boilerplate the same way, so this belongs in retrieval rather than in the
    corpus generator.

    Runs after permission filtering, never before: the survivor is always a chunk this
    caller was already entitled to see.
    """
    seen: set[str] = set()
    out: list[RetrievedChunk] = []
    for c in chunks:
        key = _text_key(c.text)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    if len(out) < len(chunks):
        log.info("dropped %d duplicate chunk(s)", len(chunks) - len(out))
    return out


def _drop_far_tail(chunks: list[RetrievedChunk], k: int) -> list[RetrievedChunk]:
    """Stop padding the context to k with chunks the cross-encoder rates far below the best.

    Asked when invoices are issued, retrieval returned the Billing FAQ and then four
    consumable standards and a scheduling FAQ, purely because k was 8 and they were next
    in line. Filler costs more than wasted tokens: the generator hung a billing claim on
    the onboarding guide because a weak, tangentially-related chunk was sitting in front
    of it.

    The cut is relative. These scores are unnormalised logits — the correct chunk for
    "what format do sample barcodes use?" scores -2.17 — so any absolute floor would
    discard every source. The gap from the best score is what separates signal from
    filler, and it is wide: -2.17 against -8.09 for the next chunk.
    """
    if not chunks:
        return chunks
    scores = [c.rerank_score for c in chunks]
    if any(s is None for s in scores):
        return chunks  # reranker unavailable; nothing to threshold on

    # A reranker that puts any chunk above zero is telling us, on a calibrated scale,
    # which chunks answer the question and which do not — Qwen3-Reranker scores the
    # log-odds of "yes". Take it at its word and drop the rest, min_keep included:
    # padding the context back out with chunks it declined is what put a second private
    # note in front of the generator, which then answered with both markers and lost the
    # whole reply to the faithfulness check. bge's logits are negative throughout, so no
    # positive score exists, the rule never fires, and its behaviour is unchanged.
    if any(s > 0 for s in scores):
        confident = [c for c in chunks if c.rerank_score > 0]
        if len(confident) < len(chunks):
            log.info("reranker is positive about %d of %d chunk(s); dropping the rest",
                     len(confident), len(chunks))
        return confident[:k]

    # The best score, not the first one: position 1 is not guaranteed to hold the highest
    # score if anything reorders, and measuring the gap from whatever happens to be on
    # top would move the floor for unrelated reasons.
    best = max(scores)
    floor = best - settings.rerank_margin
    keep = [c for c in chunks if c.rerank_score >= floor]
    if len(keep) < settings.rerank_min_keep:
        keep = chunks[: settings.rerank_min_keep]
    if len(keep) < len(chunks):
        log.info(
            "cut %d chunk(s) scoring below %.2f (best %.2f)",
            len(chunks) - len(keep), floor, best,
        )
    return keep[:k]


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
    # RERANKER_BASE_URL when set, else assume the reranker sits beside the embedder.
    base = settings.reranker_base_url or settings.embed_base_url
    url = base.rstrip("/").removesuffix("/v1") + "/rerank"
    try:
        resp = httpx.post(
            url,
            json={"query": query, "texts": [c.text for c in chunks]},
            timeout=settings.llm_timeout_s,
        )
        resp.raise_for_status()
        scored = resp.json()
        order = [(item["index"], float(item["score"])) for item in scored]
    except Exception as exc:
        log.warning("reranker unavailable (%s); keeping RRF order", exc)
        return chunks

    # The endpoint returns TEI's [{index, score}] against the order we sent. Reorder by
    # position, not by identity: chunks.index() is O(n) and wrong when two chunks compare
    # equal.
    by_index = {int(i): float(s) for i, s in order}
    ranked = sorted(
        enumerate(chunks), key=lambda pair: by_index.get(pair[0], float("-inf")), reverse=True
    )
    out = []
    for position, chunk in ranked:
        chunk.rerank_score = by_index.get(position)
        out.append(chunk)
    return out


# ---------------------------------------------------------------------------------
# Browsing the shelf.
# ---------------------------------------------------------------------------------
#
# Reading rather than searching, but the same corpus and the same permission predicate,
# so it lives beside the retriever. tests/test_rag_isolation.py enforces that: every
# statement touching echomind.chunks is in this file, which is the only way to be sure
# one of them cannot quietly grow a different idea of who may see what.

_LIBRARY_LIST_SQL = """
SELECT d.id, d.title, d.version, d.visibility, d.lab_id, d.owner_user_id,
       d.source_path, d.updated_at,
       COUNT(c.id) AS chunks,
       SUM(length(c.text)) AS characters
FROM echomind.knowledge_docs d
JOIN echomind.chunks c ON c.doc_id = d.id
WHERE {permitted}
GROUP BY d.id, d.title, d.version, d.visibility, d.lab_id, d.owner_user_id,
         d.source_path, d.updated_at
ORDER BY d.title
"""

_LIBRARY_READ_SQL = """
SELECT c.ord, c.text, c.breadcrumb
FROM echomind.chunks c
WHERE c.doc_id = :doc_id AND {permitted}
ORDER BY c.ord
"""

_LIBRARY_META_SQL = """
SELECT d.id, d.title, d.version, d.visibility, d.lab_id, d.source_path, d.updated_at
FROM echomind.knowledge_docs d
WHERE d.id = :doc_id
"""


def list_documents(ctx: Ctx) -> list[dict[str, Any]]:
    """Every document this caller could be answered from, one row each.

    Filtered by `permission_predicate` — the same clause `retrieve()` runs — rather than
    a second copy of the rule. A listing more permissive than retrieval would be a
    directory of things the reader may not have, naming the title and owner of every one.
    """
    permitted, params = permission_predicate(ctx)
    with session_scope() as s:
        rows = s.execute(
            text(_LIBRARY_LIST_SQL.format(permitted=permitted)), params
        ).mappings().all()
    return [dict(row) for row in rows]


def read_document(ctx: Ctx, doc_id: str) -> dict[str, Any] | None:
    """One document, reassembled from the chunks this caller may read.

    Reassembled rather than read from `source_path`: the file on disk is what was
    ingested, the chunks are what answers are actually drawn from, and the two can drift.
    Returns None when the caller may not read it — the same answer as "no such document",
    because which of the two it is must not be learnable.
    """
    permitted, params = permission_predicate(ctx)
    with session_scope() as s:
        chunks = s.execute(
            text(_LIBRARY_READ_SQL.format(permitted=permitted)),
            {**params, "doc_id": doc_id},
        ).mappings().all()
        if not chunks:
            return None
        meta = s.execute(text(_LIBRARY_META_SQL), {"doc_id": doc_id}).mappings().first()
    if meta is None:
        return None
    return {**dict(meta), "sections": [dict(c) for c in chunks]}
