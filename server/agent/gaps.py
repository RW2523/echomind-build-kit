"""Record every honest redirect, and rank what they add up to.

The refusal is the product's best feature and, until this existed, its most wasted
signal: a user asked something in their own words, the corpus could not support an
answer, and the system said so — then forgot. Aggregated, those refusals are a ranked
list of the documents the facility has not written yet, ordered by how many people
wanted them.

Writing here must never be able to break a turn. A refusal that fails to log is a
missing row; a refusal that raises is a broken reply to a user who asked a fair
question, so every failure here is swallowed and logged.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import text

from server.db import session_scope

log = logging.getLogger("echomind.gaps")

# Words that carry no topic. Without this "what is the warm up time" and "how long is the
# warm-up" land in different buckets and neither ever ranks.
_STOPWORDS = frozenset({
    "a", "about", "all", "an", "and", "any", "are", "as", "at", "be", "been",
    "before", "being", "both", "but", "by", "can", "could", "did", "do", "does",
    "each", "few", "for", "from", "get", "give", "had", "has", "have", "how", "i",
    "if", "in", "into", "is", "it", "its", "just", "me", "more", "most", "much",
    "my", "need", "no", "not", "now", "of", "on", "only", "or", "other", "our",
    "out", "over", "own", "please", "same", "should", "show", "so", "some", "such",
    "tell", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "those", "to", "too", "under", "up", "us", "very", "was", "we",
    "were", "what", "when", "where", "which", "who", "why", "will", "with", "would",
    "you", "your",
})
_WORD_RE = re.compile(r"[a-z0-9]+")
# "facility's" tokenises to facility + s, and the stray s changes the key for no reason.
_POSSESSIVE_RE = re.compile(r"\b(\w+)['\u2019]s\b")


def question_key(question: str) -> str:
    """A normalised form, so the same question asked fifteen ways ranks once.

    Sorted content words. Deliberately crude — it is a grouping key for a to-do list, not
    a semantic index, and a curator reading the list can see the raw questions underneath.

    It under-merges rather than over-merges: "the facility parking policy" and "parking
    policy for visitors" stay two rows because their content words differ. For a to-do
    list that is the safer error — two near-duplicate rows are obvious to a human,
    whereas two genuinely different questions merged into one hide a missing document.
    """
    text = _POSSESSIVE_RE.sub(r"\1", question.lower())
    words = sorted({w for w in _WORD_RE.findall(text) if w not in _STOPWORDS})
    return " ".join(words)


def record(question: str, *, user_id: str, role: str, reason: str,
           top_score: float | None = None, closest_doc: str | None = None) -> None:
    """Log one refusal. Never raises."""
    cleaned = (question or "").strip()
    if not cleaned:
        return
    key = question_key(cleaned)
    if not key:
        # Nothing but stopwords — "what about it?" — which is not a knowledge gap.
        return
    try:
        with session_scope() as s:
            s.execute(
                text(
                    """INSERT INTO echomind.knowledge_gaps
                           (question_key, question, user_id, role, reason, top_score, closest_doc)
                       VALUES (:key, :q, :uid, :role, :reason, :score, :doc)"""
                ),
                {"key": key, "q": cleaned[:2000], "uid": user_id, "role": role,
                 "reason": reason, "score": top_score, "doc": closest_doc},
            )
    except Exception:
        log.exception("could not record knowledge gap for %r", cleaned[:80])


def ranked(limit: int = 20, days: int = 90) -> list[dict]:
    """The to-do list: what to write next, most-wanted first.

    Ranked by distinct askers before total asks, so one person asking twelve times does
    not outrank six people asking once — the second is a far stronger case for writing
    the document.
    """
    with session_scope() as s:
        rows = s.execute(
            text(
                """SELECT question_key,
                          count(*)                       AS asked,
                          count(DISTINCT user_id)        AS askers,
                          max(created_at)                AS last_asked,
                          mode() WITHIN GROUP (ORDER BY reason)      AS usual_reason,
                          mode() WITHIN GROUP (ORDER BY question)    AS example,
                          max(closest_doc)               AS closest_doc
                     FROM echomind.knowledge_gaps
                    WHERE created_at > now() - make_interval(days => :days)
                 GROUP BY question_key
                 ORDER BY askers DESC, asked DESC, last_asked DESC
                    LIMIT :limit"""
            ),
            {"days": days, "limit": limit},
        ).mappings().all()
    return [
        {
            "question_key": r["question_key"],
            "example_question": r["example"],
            "asked": r["asked"],
            "distinct_askers": r["askers"],
            "usual_reason": r["usual_reason"],
            "closest_doc": r["closest_doc"],
            "last_asked": r["last_asked"].isoformat() if r["last_asked"] else None,
        }
        for r in rows
    ]
