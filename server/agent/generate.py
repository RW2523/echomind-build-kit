"""Grounded generation: the model composes sentences, the sources supply the facts."""

from __future__ import annotations

import logging
import re

from server.agent.llm import chat
from server.agent.responses import Citation
from server.rag.retrieval import RetrievedChunk

log = logging.getLogger("echomind.generate")

CITATION_RE = re.compile(r"\[(\d+)\]")
INSUFFICIENT = "INSUFFICIENT_CONTEXT"

SYSTEM = """You are EchoMind, the assistant for the Infinity X core-facility platform.

You answer ONLY from the numbered sources given to you. You have no other knowledge and
you never rely on any. Rules, in order of importance:

1. Every factual claim — every number, duration, price, rule, name, or status — must come
   from a source, and the sentence stating it must end with its citation, like [2].
2. If the sources do not contain the answer, reply with exactly {insufficient} and
   nothing else. Do not guess, do not hedge into a partial answer, do not use general
   knowledge about microscopy or laboratories.
3. Never invent a citation index. Only cite indices that exist in the sources.
4. Be concise and direct: two to five sentences unless the question needs a list.
5. Write in plain prose for a working scientist. No preamble, no "based on the sources".
""".format(insufficient=INSUFFICIENT)

USER = """QUESTION:
{question}

SOURCES:
{context}

Answer the question using only these sources, citing each factual sentence."""


def numbered_context(chunks: list[RetrievedChunk]) -> str:
    return "\n\n".join(
        f"[{i}] ({c.breadcrumb})\n{c.text}" for i, c in enumerate(chunks, start=1)
    )


def cited_indices(text: str, limit: int) -> list[int]:
    """Citation indices actually used, in order, discarding out-of-range hallucinations."""
    seen: list[int] = []
    for raw in CITATION_RE.findall(text):
        n = int(raw)
        if 1 <= n <= limit and n not in seen:
            seen.append(n)
    return seen


def strip_invalid_citations(text: str, limit: int) -> str:
    """Remove citation markers pointing at sources that do not exist."""
    return CITATION_RE.sub(
        lambda m: m.group(0) if 1 <= int(m.group(1)) <= limit else "", text
    ).replace("  ", " ").strip()


def build_citations(chunks: list[RetrievedChunk], indices: list[int]) -> list[Citation]:
    out = []
    for i in indices:
        c = chunks[i - 1]
        out.append(
            Citation(
                index=i,
                doc_id=c.doc_id,
                breadcrumb=c.breadcrumb,
                title=c.title,
                chunk_id=c.chunk_id,
                score=c.score,
            )
        )
    return out


def generate(question: str, chunks: list[RetrievedChunk]) -> tuple[str, list[Citation], bool]:
    """Return (text, citations, sufficient).

    sufficient=False means the model itself declined for lack of context — which the
    caller turns into a redirect, exactly like a gate failure.
    """
    if not chunks:
        return "", [], False

    raw = chat(
        [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": USER.format(question=question, context=numbered_context(chunks)),
            },
        ],
        temperature=0.0,
        max_tokens=600,
    )

    if INSUFFICIENT in raw.upper():
        log.info("generation declined for lack of context")
        return "", [], False

    text = strip_invalid_citations(raw, len(chunks))
    indices = cited_indices(text, len(chunks))
    if not indices:
        # An uncited answer is indistinguishable from an invented one. Treat it as a
        # failure rather than shipping an unsourced claim.
        log.info("generation produced no valid citations; treating as insufficient")
        return text, [], False

    return text, build_citations(chunks, indices), True
