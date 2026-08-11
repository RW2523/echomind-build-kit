"""Faithfulness: verify every claim against the chunk it cites.

The generator is instructed to cite; this checks that it told the truth about doing so.
Any unsupported claim downgrades the whole answer to a redirect — a mostly-correct answer
with one invented number is exactly the failure mode "verified or silent" exists to stop.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from server.agent.llm import chat_json
from server.agent.prompts import register
from server.agent.responses import Citation
from server.config import settings
from server.rag.retrieval import RetrievedChunk

log = logging.getLogger("echomind.faithfulness")

CITATION_RE = re.compile(r"\[(\d+)\]")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
# A sentence with no digits, no rule words and no citation is prose glue, not a claim.
FACTUAL_HINT_RE = re.compile(r"\d|must|never|always|required|charged|hours?|days?|%|\$")


JUDGE_SYSTEM = (
    "You verify whether each claim is fully supported by the sources it cites. A claim is "
    "supported only if the sources state it — not if they merely suggest it, and not if "
    "the claim adds a number, name or condition the sources do not contain.\n\n"
    "One exception, because it is the assistant's job and not a fabrication: applying a "
    "rule or threshold FROM the sources to a value the USER supplied in the question is "
    "supported. If the user asks about 12 hours and the sources say cancellations inside "
    "24 hours are charged 50%, then '12 hours before start is charged 50%' is supported — "
    "the rule and the rate both come from the sources. A value that appears in neither "
    "the sources nor the question is never supported.\n\n"
    'Reply only as JSON: {{"verdicts": [{{"id": 1, "supported": true, "why": "<12 words"}}]}}\n\n'
    "You must return exactly {count} verdict(s), one per claim, using the claim's number "
    "as its id. Do not stop early."
)


def verdict_schema(count: int) -> dict:
    """Exactly `count` verdicts, each with an id and a boolean. The decoder enforces the
    array length, so the judge cannot stop early — which is what it used to do."""
    return {
        "title": "faithfulness_verdicts",
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "supported": {"type": "boolean"},
                        "why": {"type": "string"},
                    },
                    "required": ["id", "supported", "why"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["verdicts"],
        "additionalProperties": False,
    }


def _parse_verdicts(payload: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for v in (payload or {}).get("verdicts", []) or []:
        try:
            out[int(v["id"])] = v
        except (KeyError, TypeError, ValueError):
            continue
    return out


REPAIR_SYSTEM = (
    "You locate which source states a claim. Reply supported=true only if one of the "
    "numbered sources actually states it, and give that source's number as source_id. "
    "If no source states the claim, reply supported=false with source_id 0. Do not "
    "reward a source that merely mentions the topic.\n\n"
    'Reply only as JSON: {"supported": true, "source_id": 2, "why": "<12 words"}'
)

REPAIR_SCHEMA = {
    "title": "citation_repair",
    "type": "object",
    "properties": {
        "supported": {"type": "boolean"},
        "source_id": {"type": "integer"},
        "why": {"type": "string"},
    },
    "required": ["supported", "source_id", "why"],
    "additionalProperties": False,
}


@dataclass
class ClaimVerdict:
    claim: str
    cited: list[int]
    supported: bool
    why: str = ""
    corrected_source: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FaithfulnessResult:
    passed: bool
    score: float
    checked: int
    unsupported: list[str] = field(default_factory=list)
    verdicts: list[ClaimVerdict] = field(default_factory=list)
    # (claim text, source index that actually states it) for claims the generator hung
    # on the wrong source. The caller repoints the marker before showing the answer.
    corrections: list[tuple[str, int]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 3),
            "checked": self.checked,
            "unsupported": self.unsupported,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "corrections": [
                {"claim": c, "corrected_to": i} for c, i in self.corrections
            ],
        }


def split_claims(text: str) -> list[tuple[str, list[int]]]:
    """Sentences that assert something, with the citation indices they carry."""
    claims: list[tuple[str, list[int]]] = []
    for sentence in SENTENCE_SPLIT_RE.split(text.strip()):
        s = sentence.strip()
        if not s:
            continue
        cited = [int(n) for n in CITATION_RE.findall(s)]
        bare = CITATION_RE.sub("", s).strip()
        if not bare:
            continue
        if not cited and not FACTUAL_HINT_RE.search(bare):
            continue  # framing sentence, asserts nothing checkable
        claims.append((bare, cited))
    return claims


def check(
    answer: str,
    chunks: list[RetrievedChunk],
    citations: list[Citation],
    question: str = "",
) -> FaithfulnessResult:
    """`question` lets the judge tell "applied the source's rule to what the user asked"
    apart from "invented a number" — without it, every specific answer to a specific
    question reads as unsupported."""
    claims = split_claims(answer)
    if not claims:
        return FaithfulnessResult(passed=False, score=0.0, checked=0,
                                  unsupported=["answer contained no checkable claim"])

    numbered = {i: chunks[i - 1] for i in range(1, len(chunks) + 1)}

    # An uncited claim is judged against everything retrieved, so the model cannot dodge
    # the check by simply omitting the marker.
    cited_for = {
        n: (cited or list(numbered)) for n, (_, cited) in enumerate(claims, start=1)
    }
    used = sorted({i for indices in cited_for.values() for i in indices if i in numbered})

    # Each source appears ONCE. Repeating the full chunk under every claim tripled the
    # prompt and made the 7B judge stop after the first verdict — which then read as
    # "unsupported" and suppressed a correct, properly-cited answer.
    sources_block = "\n\n".join(f"[{i}] {numbered[i].text}" for i in used)
    # Every permitted source, for the repair pass below — which has to be able to find a
    # statement in a chunk the claim did not cite.
    sources_all_block = "\n\n".join(f"[{i}] {numbered[i].text}" for i in sorted(numbered))
    claims_block = "\n".join(
        f"{n}. (cites {', '.join(f'[{i}]' for i in cited_for[n])}) {claim}"
        for n, (claim, _) in enumerate(claims, start=1)
    )

    verdict = chat_json(
        [
            {"role": "system", "content": JUDGE_SYSTEM.format(count=len(claims))},
            {
                "role": "user",
                "content": (
                    (f"QUESTION THE USER ASKED:\n{question}\n\n" if question else "")
                    + f"SOURCES:\n{sources_block}\n\nCLAIMS:\n{claims_block}"
                ),
            },
        ],
        model=settings.judge_model,
        default={"verdicts": []},
        max_tokens=800,
        schema=verdict_schema(len(claims)),
    )

    by_id = _parse_verdicts(verdict)

    # A claim the judge skipped is re-asked on its own — one claim at a time is far easier
    # for a small model. Only after that does silence count as unsupported, so the
    # fail-closed guarantee survives without a flaky judge silencing good answers.
    missing = [n for n in range(1, len(claims) + 1) if n not in by_id]
    if missing:
        log.info("judge omitted %d verdict(s); re-asking individually", len(missing))
        for n in missing:
            claim = claims[n - 1][0]
            single = chat_json(
                [
                    {"role": "system", "content": JUDGE_SYSTEM.format(count=1)},
                    {
                        "role": "user",
                        "content": (
                            (f"QUESTION THE USER ASKED:\n{question}\n\n" if question else "")
                            + "SOURCES:\n"
                            + "\n\n".join(
                                f"[{i}] {numbered[i].text}"
                                for i in cited_for[n] if i in numbered
                            )
                            + f"\n\nCLAIMS:\n1. {claim}"
                        ),
                    },
                ],
                model=settings.judge_model,
                default={"verdicts": []},
                max_tokens=200,
                schema=verdict_schema(1),
            )
            retried = _parse_verdicts(single)
            if 1 in retried:
                by_id[n] = retried[1]

    verdicts: list[ClaimVerdict] = []
    for n, (claim, cited) in enumerate(claims, start=1):
        v = by_id.get(n)
        # A claim the judge still did not rule on is not assumed good.
        supported = bool(v.get("supported")) if v else False
        why = str(v.get("why", "")) if v else "judge returned no verdict"
        verdicts.append(ClaimVerdict(claim=claim, cited=cited, supported=supported, why=why))

    # A claim can be true, present in the context, and still fail — because the generator
    # hung it on the wrong source number. "Each invoice covers one account code for one
    # calendar month" was cited to the onboarding guide, which mentions first invoices in
    # passing, while the Billing FAQ that states it verbatim sat at [1] in the same
    # context. Suppressing the whole answer for that is a false negative: the reader loses
    # a correct, fully sourced answer over a misplaced bracket.
    #
    # Only claims that carried a citation are re-checked, and only against chunks already
    # retrieved and permission-filtered for this caller — the evidence bar is unchanged.
    # What changes is that we repoint the citation instead of discarding the answer. A
    # claim no permitted source states still fails, which is the guarantee that matters.
    corrections: list[tuple[str, int]] = []
    for v in verdicts:
        if v.supported or not v.cited or len(numbered) <= len(v.cited):
            continue
        found = chat_json(
            [
                {"role": "system", "content": REPAIR_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        (f"QUESTION THE USER ASKED:\n{question}\n\n" if question else "")
                        + f"SOURCES:\n{sources_all_block}\n\nCLAIM:\n{v.claim}"
                    ),
                },
            ],
            model=settings.judge_model,
            default={"supported": False, "source_id": 0},
            max_tokens=200,
            schema=REPAIR_SCHEMA,
        )
        source_id = found.get("source_id")
        if (
            found.get("supported")
            and isinstance(source_id, int)
            and source_id in numbered
            and source_id not in v.cited
        ):
            log.info("claim cited %s but is stated in [%d]; repointing", v.cited, source_id)
            v.supported = True
            v.corrected_source = source_id
            v.why = f"stated in [{source_id}], not in the source it cited"
            corrections.append((v.claim, source_id))

    supported_count = sum(1 for v in verdicts if v.supported)
    score = supported_count / len(verdicts)
    unsupported = [v.claim for v in verdicts if not v.supported]

    passed = score >= settings.faithfulness_min and not unsupported
    log.info(
        "faithfulness score=%.2f checked=%d unsupported=%d passed=%s",
        score, len(verdicts), len(unsupported), passed,
    )
    return FaithfulnessResult(
        passed=passed, score=score, checked=len(verdicts),
        unsupported=unsupported, verdicts=verdicts, corrections=corrections,
    )

# Versioned by content hash — see server/agent/prompts.py.
VERSION_JUDGE_SYSTEM = register("faithfulness.judge", JUDGE_SYSTEM)
VERSION_REPAIR_SYSTEM = register("faithfulness.repair", REPAIR_SYSTEM)
