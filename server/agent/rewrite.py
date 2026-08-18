"""Resolve a follow-up into a question that stands on its own.

Retrieval sees only the words it is given. "How long is that?" after a question about
confocal lasers retrieves on "how long is that" — five stopwords and a pronoun — and the
gate then correctly refuses, because nothing in the corpus matches. The conversation was
in the state all along; it just never reached the retriever.

The rewrite is used for retrieval and for judging. What the user asked is what the user
sees: the transcript still shows their words, not ours.

Conservative by construction. A question that already stands alone is returned untouched
without a model call, and any failure returns the original — a follow-up that retrieves
badly is a redirect, which is recoverable, while a rewrite that changes the meaning is a
confident answer to a question nobody asked.
"""

from __future__ import annotations

import logging
import re

from server.agent.llm import chat_json
from server.agent.prompts import register
from server.config import settings

log = logging.getLogger("echomind.rewrite")

# Signals that a question leans on what came before. Cheap to check, and it keeps the
# model out of the loop for the large majority of turns that need nothing.
REFERENTIAL_WORDS = frozenset({
    "it", "its", "that", "this", "those", "these", "they", "them", "their", "there",
    "then", "he", "she", "his", "her", "same", "one", "ones", "another", "others",
    "above", "previous", "instead",
})
_DEPENDENT_RE = re.compile(
    r"\b(" + "|".join(sorted(REFERENTIAL_WORDS)) + r")\b", re.IGNORECASE
)
# "And the cost?" — no pronoun, but plainly a continuation.
_CONTINUATION_RE = re.compile(
    r"^\s*(and|but|so|also|what about|how about|ok|okay)\b", re.IGNORECASE
)
MAX_WORDS_ALWAYS_REWRITE = 4

SYSTEM = (
    "The user's latest message depends on the conversation before it. Rewrite it as a "
    "question that stands on its own.\n\n"
    "Replace every pronoun and reference with what it points at, and change nothing "
    "else: do not answer it, do not add detail the user did not give, do not broaden or "
    "narrow what was asked. Keep it one sentence.\n\n"
    "Example — conversation: 'How long must the confocal lasers warm up? / They must "
    "warm up for 30 minutes.' Latest message: 'And is that before or after alignment?' "
    "Rewrite: 'Is the 30-minute confocal laser warm-up before or after alignment?'\n\n"
    "The caller has already decided this message needs resolving, so returning it "
    "unchanged is not useful — find what it refers to.\n"
    'Reply only as JSON: {"question": "..."}'
)

SCHEMA = {
    "title": "standalone_question",
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
    "additionalProperties": False,
}


def needs_rewrite(question: str, history: str) -> bool:
    if not history.strip() or not question.strip():
        return False
    if _DEPENDENT_RE.search(question) or _CONTINUATION_RE.match(question):
        return True
    # "The warm-up?" carries no pronoun and still cannot be retrieved on.
    return len(question.split()) <= MAX_WORDS_ALWAYS_REWRITE


def refers_back(question: str, history: str) -> bool:
    """Does this message actually lean on the turn before it?

    The strict half of needs_rewrite, without its length rule. Retrieval wants that rule:
    "The warm-up?" carries no pronoun, retrieves on two stopwords, and a rewrite is the
    only thing that saves it — a bad rewrite there costs a redirect, which is recoverable.

    A lookup pays differently. "Show me Alice's bookings" is four words and perfectly
    clear, and being rewritten against a previous turn about a hypoxia note produced "The
    private marker in your hypoxia timecourse note is not present in the records" — an
    answer to a question nobody asked, in a conversation whose entire point was refusing
    to answer about someone else's records. Shortness is not dependence.
    """
    if not history.strip() or not question.strip():
        return False
    return bool(_DEPENDENT_RE.search(question) or _CONTINUATION_RE.match(question))


def is_unresolvable(question: str, history: str) -> bool:
    """A message that is almost nothing but a reference, with no conversation behind it.

    Narrow on purpose. The first version flagged any dependent marker or any short
    question, and immediately broke two real golden questions: "what am I charged if I
    cancel a booking 12 hours before it starts?" contains "it", and "when are invoices
    issued?" is four words. Both are perfectly clear.

    What actually distinguishes "Is it optional?" is that once the stopwords and the
    reference itself are removed there is no subject left — nothing to look up. A
    question carrying two or more content words names its own topic, whatever pronouns
    it also happens to use.
    """
    if history.strip() or not question.strip():
        return False
    if not (_DEPENDENT_RE.search(question) or _CONTINUATION_RE.match(question)):
        return False
    return len(_content_words(question)) <= 1


def _content_words(question: str) -> set[str]:
    """Words that could name a topic: not stopwords, not the reference itself."""
    from server.agent.gaps import _STOPWORDS, _WORD_RE

    return {
        w for w in _WORD_RE.findall(question.lower())
        if w not in _STOPWORDS and w not in REFERENTIAL_WORDS and len(w) > 1
    }


def standalone(question: str, history: str) -> str:
    """The question with its references resolved, or the original."""
    if not needs_rewrite(question, history):
        return question

    result = chat_json(
        [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": (
                    f"CONVERSATION SO FAR:\n{history}\n\nLATEST MESSAGE:\n{question}"
                ),
            },
        ],
        model=settings.judge_model,
        default={"question": question},
        max_tokens=160,
        schema=SCHEMA,
    )
    rewritten = str(result.get("question") or "").strip()
    if not rewritten:
        return question

    # A rewrite that drops most of the original is a rewrite that changed the subject.
    # Length is a blunt guard, but it catches the failure that matters — the model
    # answering instead of rewriting — without needing a second judge.
    if len(rewritten) > 400 or len(rewritten.split()) > 60:
        log.info("discarding an over-long rewrite (%d words)", len(rewritten.split()))
        return question

    if rewritten.lower() != question.lower():
        log.info("rewrote %r -> %r", question[:60], rewritten[:60])
    return rewritten

# Versioned by content hash — see server/agent/prompts.py.
VERSION_SYSTEM = register("rewrite.system", SYSTEM)
