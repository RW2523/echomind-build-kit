"""A deterministic OpenAI-compatible /embeddings endpoint, for CI.

    python -m scripts.stub_embeddings &
    EMBED_BASE_URL=http://localhost:8099/v1 python -m server.rag.ingest db/corpus

Why this exists. The permission filter in `server/rag/retrieval.py` is enforcement point
3 of spec 05 — the thing standing between one lab's protocols and another's — and it had
no coverage in CI at all, because ingesting the corpus needs an embedding model and a
GitHub runner has no GPU. The isolation tests assert *who* comes back, never *how well
ranked*, so they do not need a real embedding space; they need vectors that are stable
and distinguish documents. That is what this serves.

It is emphatically not a semantic model: similarity here is bag-of-words overlap and
nothing more. Retrieval *quality* is measured by `make eval` against the real model on a
machine that has one. What CI gets from this is that the SQL predicate, the visibility
rules and the lab scoping all still do what they claim.

The application is unchanged and unaware — this is another OpenAI-compatible endpoint,
which is exactly the seam the code already has ("Code never branches on which — only
EMBED_BASE_URL and EMBED_MODEL change").
"""

from __future__ import annotations

import hashlib
import math
import os
import re

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

PORT = int(os.environ.get("STUB_EMBED_PORT", "8099"))
# Must match EMBED_DIM, or pgvector rejects the insert.
DIM = int(os.environ.get("STUB_EMBED_DIM", "1024"))

WORD_RE = re.compile(r"[a-z0-9]+")

app = FastAPI(title="echomind-stub-embeddings")


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None


def _vector(text: str) -> list[float]:
    """A hashed bag of words, L2-normalised.

    Every word lands in a fixed bucket, so two documents sharing vocabulary end up with a
    high cosine and two that share none end up near zero. Deterministic across processes
    and runs — no seeding, no model download, same answer on every machine.
    """
    vec = [0.0] * DIM
    for word in WORD_RE.findall(text.lower()):
        digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % DIM
        # The second half of the digest decides the sign, so unrelated words do not all
        # push in the same direction and collapse every document towards one vector.
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[bucket] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        # An empty or punctuation-only string still needs a unit vector: pgvector's
        # cosine operator divides by the norm and NaN would poison every comparison.
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]


@app.get("/health")
def health() -> dict:
    return {"ok": True, "dim": DIM, "kind": "stub"}


@app.post("/v1/embeddings")
@app.post("/embeddings")
def embeddings(req: EmbeddingRequest) -> dict:
    texts = [req.input] if isinstance(req.input, str) else list(req.input)
    return {
        "object": "list",
        "model": req.model or "stub",
        "data": [
            {"object": "embedding", "index": i, "embedding": _vector(t)}
            for i, t in enumerate(texts)
        ],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
