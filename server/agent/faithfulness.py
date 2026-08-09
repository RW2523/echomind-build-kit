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


@dataclass
class ClaimVerdict:
    claim: str
    cited: list[int]
    supported: bool
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FaithfulnessResult:
    passed: bool
    score: float
    checked: int
    unsupported: list[str] = field(default_factory=list)
    verdicts: list[ClaimVerdict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 3),
            "checked": self.checked,
            "unsupported": self.unsupported,
            "verdicts": [v.to_dict() for v in self.verdicts],
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
) -> FaithfulnessResult:
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
    claims_block = "\n".join(
        f"{n}. (cites {', '.join(f'[{i}]' for i in cited_for[n])}) {claim}"
        for n, (claim, _) in enumerate(claims, start=1)
    )

    verdict = chat_json(
        [
            {"role": "system", "content": JUDGE_SYSTEM.format(count=len(claims))},
            {
                "role": "user",
                "content": f"SOURCES:\n{sources_block}\n\nCLAIMS:\n{claims_block}",
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
                            "SOURCES:\n"
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
        unsupported=unsupported, verdicts=verdicts,
    )
