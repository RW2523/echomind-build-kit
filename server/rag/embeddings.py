"""Embeddings via an OpenAI-compatible /embeddings endpoint.

Dev profile points at Ollama (bge-m3, 1024-d); the Spark profile points at vLLM/TEI.
Code never branches on which — only EMBED_BASE_URL and EMBED_MODEL change.
"""

from __future__ import annotations

import logging

from openai import OpenAI

from server.config import settings

log = logging.getLogger("echomind.embeddings")

BATCH = 32

_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=settings.embed_base_url,
            api_key="local",  # local endpoints ignore it; the SDK requires something
            timeout=settings.llm_timeout_s,
        )
    return _client


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts, preserving order."""
    if not texts:
        return []
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i : i + BATCH]
        resp = client().embeddings.create(model=settings.embed_model, input=batch)
        # The API does not guarantee ordering; index is authoritative.
        for item in sorted(resp.data, key=lambda d: d.index):
            out.append(list(item.embedding))
    if out and len(out[0]) != settings.embed_dim:
        raise RuntimeError(
            f"{settings.embed_model} returned {len(out[0])}-d vectors but the chunks "
            f"table stores vector({settings.embed_dim}). Re-create the column or change "
            f"EMBED_MODEL."
        )
    return out


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def to_pgvector(vec: list[float]) -> str:
    """pgvector's text input format."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"
