"""The confidence gate: the thing that makes "verified or silent" real.

Three checks, in increasing cost order (spec 04 §2):

  1. score floor  — cheap, arithmetic. Top cosine similarity >= GATE_MIN_TOP_SCORE.
  2. coverage     — one cheap LLM yes/no: do these chunks actually contain the answer?
  3. agreement    — only evaluated when the retrieved chunks appear to conflict.

Failing any check produces an honest redirect naming what could not be verified and who
to ask, never a guess. Thresholds come from env so the Spark profile can retune without
a code change.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from server.agent.llm import chat_json
from server.config import settings
from server.rag.retrieval import RetrievedChunk

log = logging.getLogger("echomind.gate")

# Which role owns which kind of question, for the redirect's "ask this person" line.
TOPIC_OWNER = [
    (("billing", "invoice", "charge", "cost", "account code", "spend", "price", "rate"),
     "the core facility admin"),
    (("protocol", "dilution", "antibody", "staining", "our lab", "house"),
     "your PI"),
    (("training", "certification", "biosafety", "access", "badge", "onboard"),
     "the core facility admin"),
]
DEFAULT_OWNER = "the core facility admin"


@dataclass
class GateResult:
    passed: bool
    reason: str
    top_score: float
    coverage: bool | None = None
    agreement: bool | None = None
    conflict_checked: bool = False
    closest_breadcrumb: str | None = None
    thresholds: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _owner_for(question: str) -> str:
    q = question.lower()
    for keywords, owner in TOPIC_OWNER:
        if any(k in q for k in keywords):
            return owner
    return DEFAULT_OWNER


def _numbered_context(chunks: list[RetrievedChunk]) -> str:
    return "\n\n".join(
        f"[{i}] ({c.breadcrumb})\n{c.text}" for i, c in enumerate(chunks, start=1)
    )


def _coverage_check(question: str, chunks: list[RetrievedChunk]) -> bool:
    """Is the answer actually in here?

    Phrased as "point at the source" rather than an abstract yes/no: a small local model
    is markedly more reliable at selecting a passage than at judging sufficiency, and the
    selection doubles as evidence. 0 means nothing covers it.
    """
    verdict = chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You locate the source passage that answers a question. You do not "
                    "answer the question yourself.\n\n"
                    'Reply only as JSON: {"source": <number>, "quote": "<= 15 words"}\n\n'
                    "Set source to the number of the passage that states the answer, and "
                    "quote the words in it that do so. If no passage states the answer, "
                    "set source to 0 and quote to \"\". A passage that is merely about "
                    "the same topic does not count."
                ),
            },
            {
                "role": "user",
                "content": f"QUESTION:\n{question}\n\nSOURCES:\n{_numbered_context(chunks)}",
            },
        ],
        default={"source": 0, "quote": ""},
        max_tokens=150,
    )
    try:
        picked = int(verdict.get("source") or 0)
    except (TypeError, ValueError):
        picked = 0
    return 1 <= picked <= len(chunks)


def _agreement_check(question: str, chunks: list[RetrievedChunk]) -> tuple[bool, bool]:
    """Return (conflict_checked, agree).

    Only run when the sources look like they might disagree; a single source cannot
    conflict with itself, so the common case costs nothing.
    """
    if len(chunks) < 2:
        return False, True

    verdict = chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You check whether source passages contradict each other on the "
                    "specific point a question asks about. Differing topics are not a "
                    'contradiction. Reply only as JSON: '
                    '{"conflict": true|false, "detail": "<15 words"}.'
                ),
            },
            {
                "role": "user",
                "content": f"QUESTION:\n{question}\n\nSOURCES:\n{_numbered_context(chunks)}",
            },
        ],
        default={"conflict": False, "detail": "judge unavailable"},
        max_tokens=120,
    )
    conflict = bool(verdict.get("conflict"))
    return True, not conflict


def evaluate(question: str, chunks: list[RetrievedChunk]) -> GateResult:
    thresholds = {
        "min_top_score": settings.gate_min_top_score,
        "min_agreement": settings.gate_min_agreement,
        "reranker": settings.reranker,
    }
    closest = chunks[0].breadcrumb if chunks else None

    if not chunks:
        return GateResult(
            passed=False, reason="no_permitted_sources", top_score=0.0,
            closest_breadcrumb=None, thresholds=thresholds,
        )

    top_score = max(c.score for c in chunks)
    if top_score < settings.gate_min_top_score:
        return GateResult(
            passed=False, reason="below_score_floor", top_score=top_score,
            closest_breadcrumb=closest, thresholds=thresholds,
        )

    if not _coverage_check(question, chunks):
        return GateResult(
            passed=False, reason="no_coverage", top_score=top_score, coverage=False,
            closest_breadcrumb=closest, thresholds=thresholds,
        )

    conflict_checked, agree = _agreement_check(question, chunks)
    if not agree:
        return GateResult(
            passed=False, reason="sources_disagree", top_score=top_score, coverage=True,
            agreement=False, conflict_checked=True, closest_breadcrumb=closest,
            thresholds=thresholds,
        )

    return GateResult(
        passed=True, reason="ok", top_score=top_score, coverage=True, agreement=agree,
        conflict_checked=conflict_checked, closest_breadcrumb=closest,
        thresholds=thresholds,
    )


REASON_TEXT = {
    "no_permitted_sources": (
        "I could not find anything you have access to that covers this."
    ),
    "below_score_floor": (
        "I found related material, but nothing close enough for me to treat as an answer."
    ),
    "no_coverage": (
        "I found related material, but it does not actually state the answer."
    ),
    "sources_disagree": (
        "The sources I can see disagree on this point, so I will not pick one for you."
    ),
    "unfaithful": (
        "I drafted an answer but could not verify every claim against the sources, "
        "so I am not going to give it to you."
    ),
}


def redirect_text(question: str, gate: GateResult) -> str:
    """The honest redirect: what is unverified, and where to go instead."""
    lines = [REASON_TEXT.get(gate.reason, REASON_TEXT["no_coverage"])]

    if gate.closest_breadcrumb and gate.reason != "no_permitted_sources":
        lines.append(f"The closest document I can see is: {gate.closest_breadcrumb}.")

    lines.append(f"For a definitive answer, ask {_owner_for(question)}.")
    lines.append(
        "I would rather tell you I don't know than give you a number I cannot show you "
        "the source for."
    )
    return " ".join(lines)
