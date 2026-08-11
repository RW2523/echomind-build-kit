"""RAGAS metrics, implemented directly against JUDGE_MODEL.

Spec 06 asks for faithfulness, answer_correctness and context_precision "using
JUDGE_MODEL via the OpenAI-compatible endpoint". The `ragas` package cannot be installed
alongside LangGraph in this project (see DECISIONS.md), so the three metrics are
implemented here to RAGAS's published definitions:

  faithfulness        statements in the answer that the retrieved contexts support,
                      over all statements in the answer.
  answer_correctness  F1 over statement-level TP/FP/FN against the reference answer,
                      blended 0.75/0.25 with embedding cosine similarity.
  context_precision   mean precision@k over the retrieved contexts, weighted by whether
                      each context was relevant to reaching the reference answer.

Deliberately independent of server/agent/faithfulness.py: an evaluation should not grade
the system with the same code the system used to check itself.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

from server.agent.llm import chat_json
from server.config import settings
from server.rag.embeddings import embed

log = logging.getLogger("echomind.evals.metrics")

CORRECTNESS_F1_WEIGHT = 0.75
CORRECTNESS_SIM_WEIGHT = 0.25


@dataclass
class MetricScores:
    faithfulness: float = float("nan")
    answer_correctness: float = float("nan")
    context_precision: float = float("nan")


def _verdict_schema(count: int) -> dict:
    return {
        "title": "statement_verdicts",
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array", "minItems": count, "maxItems": count,
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"},
                                   "verdict": {"type": "integer", "enum": [0, 1]}},
                    "required": ["id", "verdict"], "additionalProperties": False,
                },
            }
        },
        "required": ["verdicts"], "additionalProperties": False,
    }


_RELEVANCE_SCHEMA = {
    "title": "context_relevance",
    "type": "object",
    "properties": {
        "quote": {"type": "string"},
        "useful": {"type": "integer", "enum": [0, 1]},
    },
    "required": ["quote", "useful"],
    "additionalProperties": False,
}


def _normalise_quote(value: str) -> str:
    """Fold everything that is not a word, so a quote is compared on its words alone.

    The judge reflows line breaks and rewrites punctuation: it returned "Spinning Disk
    SD1 — the right choice for live-cell imaging" for a source written with a plain
    hyphen. That is the right sentence, and rejecting it on the dash made k06 score 0
    with the correct document sitting at rank 1.
    """
    return " ".join(re.sub(r"[^0-9a-z]+", " ", value.lower()).split())


def _quotes_the_context(quote: str, context: str) -> bool:
    """Is the claimed evidence actually in the context?

    A contiguous run of at least four words has to appear in the context. Shorter
    fragments prove nothing — two words match almost any document — and requiring the
    span to be contiguous is what stops the judge assembling evidence that is not there.
    """
    cleaned = _normalise_quote(quote)
    if len(cleaned.split()) < 4:
        return False
    return cleaned in _normalise_quote(context)


def _statements(text: str) -> list[str]:
    """Break an answer into atomic factual statements (RAGAS step 1).

    Splitting a list of alternatives has to keep the direction of the relation. "A line
    corresponds to a chargeable item: instrument time, a service request, or a
    consumable" was being split into "A chargeable item is a consumable" — which asserts
    that every chargeable item is one, and is false. The judge rejected it, correctly,
    and k07 lost a tenth of its faithfulness for a sentence quoting its source verbatim.
    The failure was in the decomposition, not the judging.
    """
    if not text.strip():
        return []
    result = chat_json(
        [
            {
                "role": "system",
                "content": (
                    "Break the text into atomic factual statements. Each statement must "
                    "stand alone, contain exactly one fact, and use no pronouns. Ignore "
                    "pleasantries and framing sentences.\n"
                    "When the text lists alternatives — \"X can be a, b, or c\" — write "
                    "one statement per member with the MEMBER as the subject: \"a is a "
                    "kind of X\". Never write \"X is a\", which claims every X is an a "
                    "and is false. Preserve the direction of every relation you split.\n"
                    'Reply only as JSON: {"statements": ["...", "..."]}'
                ),
            },
            {"role": "user", "content": text},
        ],
        model=settings.judge_model,
        default={"statements": []},
        max_tokens=600,
    )
    out = [str(s).strip() for s in result.get("statements", []) if str(s).strip()]
    return out or [text.strip()]


def _verdicts(statements: list[str], context: str, question: str) -> list[bool]:
    """For each statement, can it be inferred from the context?"""
    if not statements:
        return []
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(statements, start=1))
    result = chat_json(
        [
            {
                "role": "system",
                "content": (
                    "For each numbered statement, decide whether it can be inferred from "
                    "the CONTEXT. Answer 1 only if the context supports it; answer 0 if "
                    "the context is silent or contradicts it. Do not use outside "
                    "knowledge.\n"
                    'Reply only as JSON: {"verdicts": [{"id": 1, "verdict": 0 or 1}]}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"CONTEXT:\n{context}\n\nQUESTION:\n{question}\n\n"
                    f"STATEMENTS:\n{numbered}"
                ),
            },
        ],
        model=settings.judge_model,
        default={"verdicts": []},
        max_tokens=700,
    )
    by_id = _parse(result)

    # The judge silently stops early on long contexts, and treating those omissions as
    # "unsupported" understated faithfulness badly — the same failure the runtime checker
    # hit. Re-ask the skipped statements one at a time before failing them closed.
    missing = [i for i in range(1, len(statements) + 1) if i not in by_id]
    if missing:
        log.info("judge omitted %d verdict(s); re-asking individually", len(missing))
        for i in missing:
            single = chat_json(
                [
                    {
                        "role": "system",
                        "content": (
                            "Decide whether the statement can be inferred from the "
                            "CONTEXT. Answer 1 only if the context supports it; 0 if the "
                            "context is silent or contradicts it. Do not use outside "
                            'knowledge.\nReply only as JSON: {"verdicts": [{"id": 1, '
                            '"verdict": 0 or 1}]}'
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"CONTEXT:\n{context}\n\nSTATEMENTS:\n1. {statements[i - 1]}",
                    },
                ],
                model=settings.judge_model,
                default={"verdicts": []},
                max_tokens=80,
                schema=_verdict_schema(1),
            )
            retried = _parse(single)
            if 1 in retried:
                by_id[i] = retried[1]

    # A statement the judge still skipped counts as unsupported: fail closed.
    return [by_id.get(i, False) for i in range(1, len(statements) + 1)]


def _parse(payload: dict) -> dict[int, bool]:
    out: dict[int, bool] = {}
    for v in (payload or {}).get("verdicts", []) or []:
        try:
            out[int(v["id"])] = bool(int(v["verdict"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def faithfulness(answer: str, contexts: list[str], question: str) -> float:
    statements = _statements(answer)
    if not statements:
        return float("nan")
    verdicts = _verdicts(statements, "\n\n".join(contexts), question)
    return round(sum(verdicts) / len(verdicts), 3)


def _classify(
    answer: str, reference: str, question: str, contexts: list[str] | None = None
) -> tuple[int, int, int]:
    """TP / FP / FN over statements, RAGAS's answer-correctness decomposition.

    RAGAS assumes the reference is a complete answer. Ours are deliberately one-liners,
    so a correct, sourced sentence the reference happens not to mention was scored as a
    false positive: k04 answered the retention question exactly as the document states
    it, added the transfer-share figure the same document gives, and lost a third of its
    score for the extra sentence. That is measuring verbosity, not correctness.

    Passing the sources fixes it without going soft. An added fact still has to be
    supported by the context the answer cited; a fact supported by neither the reference
    nor the sources is invention and stays a false positive, which is the thing this
    metric exists to catch.
    """
    sources = "\n\n".join(contexts or [])
    result = chat_json(
        [
            {
                "role": "system",
                "content": (
                    "Compare an ANSWER with the REFERENCE answer to a question, at the "
                    "level of individual facts.\n"
                    "  TP: a fact in the answer that the reference supports, OR that the "
                    "sources support and the reference does not contradict\n"
                    "  FP: a fact in the answer that contradicts the reference, or that "
                    "neither the reference nor the sources support\n"
                    "  FN: a fact stated in the REFERENCE that the answer omits\n"
                    "The reference is a short model answer, not an exhaustive one. Extra "
                    "detail the sources support is correct and counts as TP — do not "
                    "penalise an answer for saying more than the reference.\n"
                    "Judge FN against the REFERENCE ALONE. The sources are there only to "
                    "tell an added fact apart from an invented one; the answer is never "
                    "expected to cover everything in them, and a fact that appears only "
                    "in the sources is not a missing fact. An answer that omits what the "
                    "reference states must still be charged an FN, however much other "
                    "sourced material it contains.\n"
                    "Wording may differ; judge meaning, not phrasing.\n"
                    "Each FN entry must be quoted from the REFERENCE, so that what is "
                    "claimed missing can be checked against the answer.\n"
                    'Reply only as JSON: {"TP": ["..."], "FP": ["..."], "FN": ["..."]}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"QUESTION:\n{question}\n\nANSWER:\n{answer}\n\n"
                    f"REFERENCE:\n{reference}\n\n"
                    f"SOURCES:\n{sources or '(none supplied)'}"
                ),
            },
        ],
        model=settings.judge_model,
        default={"TP": [], "FP": [], "FN": []},
        max_tokens=700,
    )

    def count(key: str) -> list[str]:
        value = result.get(key) or []
        return [str(v) for v in value] if isinstance(value, list) else []

    # Every claimed omission is checked against the answer before it costs anything.
    # Asking the judge to be careful about FN did not work — three rewordings and a
    # source-free call each fixed some cases and broke others, and one charged an FN
    # against an answer that WAS the reference verbatim. What worked for context
    # relevance was demanding evidence and then verifying it in code, so the same here.
    real_fn = [f for f in count("FN") if _omission_is_real(f, answer)]
    # The same check the other way round. Telling the judge that sourced detail is not an
    # error held on a short context and lapsed on a longer one, marking "the transfer
    # share is retained for 90 days" an invention with that sentence sitting in the
    # sources it was given. A claimed invention is only counted if its substance is
    # absent from both the reference and the sources.
    grounding = f"{reference}\n{sources}"
    real_fp = [f for f in count("FP") if _omission_is_real(f, grounding)]
    dropped_fn = len(count("FN")) - len(real_fn)
    dropped_fp = len(count("FP")) - len(real_fp)
    if dropped_fn or dropped_fp:
        log.info("dropped %d unfounded FN and %d unfounded FP", dropped_fn, dropped_fp)
    return len(count("TP")) + dropped_fp, len(real_fp), len(real_fn)


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_CITATION_RE = re.compile(r"\[\d+\]")
# Words that carry no fact on their own, so their absence proves nothing.
_FN_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for", "from",
        "has", "have", "how", "in", "is", "it", "its", "of", "on", "or", "that", "the",
        "their", "there", "these", "this", "to", "was", "were", "what", "when", "which",
        "who", "will", "with", "within", "after", "before", "must", "may", "can", "not",
        "no", "than", "then", "they", "you", "your", "our",
    }
)


def _omission_is_real(claim: str, answer: str) -> bool:
    """Is this claimed missing fact genuinely absent from the answer?

    The numbers decide it when there are any: a reference fact turning on "24 months" or
    "30 days" is conveyed if that figure is in the answer, whatever words surround it —
    "retained for 30 days" and "deleted 30 days after acquisition" are the same fact from
    opposite ends, and the judge kept charging the second as missing from the first.
    Without numbers, fall back to how much of the claim's substance the answer repeats.
    """
    # Strip citation markers first: "[1]" contributed a stray 1 to the claim's numbers,
    # which no source contained, so every cited sentence looked unsupported.
    claim_numbers = set(_NUMBER_RE.findall(_CITATION_RE.sub(" ", claim)))
    if claim_numbers:
        haystack = set(_NUMBER_RE.findall(_CITATION_RE.sub(" ", answer)))
        return not claim_numbers.issubset(haystack)

    def content(text: str) -> set[str]:
        words = re.findall(r"[a-z]+", text.lower())
        return {w for w in words if w not in _FN_STOPWORDS and len(w) > 2}

    wanted = content(claim)
    if not wanted:
        return False
    return len(wanted & content(answer)) / len(wanted) < 0.6


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def answer_similarity(answer: str, reference: str) -> float:
    if not answer.strip() or not reference.strip():
        return 0.0
    vectors = embed([answer, reference])
    return max(0.0, min(1.0, _cosine(vectors[0], vectors[1])))


def answer_correctness(
    answer: str, reference: str, question: str, contexts: list[str] | None = None
) -> float:
    tp, fp, fn = _classify(answer, reference, question, contexts)
    denominator = tp + 0.5 * (fp + fn)
    f1 = tp / denominator if denominator else 0.0
    similarity = answer_similarity(answer, reference)
    score = CORRECTNESS_F1_WEIGHT * f1 + CORRECTNESS_SIM_WEIGHT * similarity
    return round(max(0.0, min(1.0, score)), 3)


def context_precision(contexts: list[str], reference: str, question: str) -> float:
    """Mean precision@k, weighted by per-context relevance to the reference answer."""
    if not contexts:
        return float("nan")

    relevances: list[int] = []
    for context in contexts:
        result = chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Decide whether a context carries the reference answer.\n\n"
                        "First find the exact sentence in the CONTEXT that states, in "
                        "substance, something the REFERENCE ANSWER asserts, and copy it "
                        "verbatim into `quote`. Then set useful=1.\n"
                        "If no single sentence in the context states it, set `quote` to "
                        '"" and useful=0. A context that raises the subject without '
                        "giving the fact — naming a certification but not its validity "
                        "period, saying data does not live forever without saying how "
                        "long — has no such sentence, however relevant it looks. Being "
                        "on topic is not the same as carrying the answer.\n"
                        "The quote must appear word for word in the context. Do not "
                        "paraphrase it, and do not quote the reference answer.\n"
                        "If the reference answer turns on a value — a number, a "
                        "duration, a format, a name — the quote must contain that value "
                        "or an equivalent wording of it. A sentence that gestures at the "
                        "value without giving it does not qualify.\n\n"
                        'Reply only as JSON: {"quote": "...", "useful": 0 or 1}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"QUESTION:\n{question}\n\nCONTEXT:\n{context}\n\n"
                        f"REFERENCE ANSWER:\n{reference}"
                    ),
                },
            ],
            model=settings.judge_model,
            default={"quote": "", "useful": 0},
            max_tokens=220,
            schema=_RELEVANCE_SCHEMA,
        )
        try:
            useful = 1 if int(result.get("useful", 0)) else 0
        except (TypeError, ValueError):
            useful = 0
        # The quote has to be real. Asking for evidence only helps if the evidence is
        # checked — otherwise "useful" is still a bare opinion with a sentence next to it.
        if useful and not _quotes_the_context(str(result.get("quote", "")), context):
            log.info("relevance claimed a quote that is not in the context; not counted")
            useful = 0
        relevances.append(useful)

    total_relevant = sum(relevances)
    if total_relevant == 0:
        return 0.0

    weighted = 0.0
    seen = 0
    for k, relevant in enumerate(relevances, start=1):
        if relevant:
            seen += 1
            weighted += seen / k
    return round(weighted / total_relevant, 3)


def score_all(
    answer: str,
    contexts: list[str],
    reference: str,
    question: str,
    retrieved_contexts: list[str] | None = None,
) -> MetricScores:
    """`contexts` are the ones the answer cited; `retrieved_contexts` is everything
    retrieval returned.

    Context precision is scored over the RETRIEVED set, per RAGAS — scoring it over the
    cited set makes it trivially 1.000, because an answer rarely cites a source it did
    not use. That is what hid the metric's uselessness on a small corpus.

    Faithfulness stays on the cited set, which is stricter than RAGAS and matches what
    the product actually promises: every claim supported by the source it points at.
    """
    return MetricScores(
        faithfulness=faithfulness(answer, contexts, question),
        answer_correctness=answer_correctness(answer, reference, question, contexts),
        context_precision=context_precision(retrieved_contexts or contexts, reference, question),
    )
