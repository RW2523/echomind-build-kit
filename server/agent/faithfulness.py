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
    payload = []
    for n, (claim, cited) in enumerate(claims, start=1):
        # An uncited claim is judged against everything retrieved, so the model cannot
        # dodge the check by simply omitting the marker.
        sources = cited or list(numbered)
        payload.append(
            {
                "id": n,
                "claim": claim,
                "sources": "\n\n".join(
                    f"[{i}] {numbered[i].text}" for i in sources if i in numbered
                ),
            }
        )

    blocks = "\n\n---\n\n".join(
        f"CLAIM {p['id']}: {p['claim']}\nCITED SOURCES:\n{p['sources']}" for p in payload
    )

    verdict = chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You verify whether each claim is fully supported by its cited "
                    "sources. A claim is supported only if the sources state it — not if "
                    "they merely suggest it, and not if the claim adds a number, name or "
                    "condition the sources do not contain. Reply only as JSON: "
                    '{"verdicts": [{"id": 1, "supported": true|false, '
                    '"why": "<12 words"}]}. Judge every claim.'
                ),
            },
            {"role": "user", "content": blocks},
        ],
        model=settings.judge_model,
        default={"verdicts": []},
        max_tokens=800,
    )

    by_id = {}
    for v in verdict.get("verdicts", []):
        try:
            by_id[int(v["id"])] = v
        except (KeyError, TypeError, ValueError):
            continue

    verdicts: list[ClaimVerdict] = []
    for n, (claim, cited) in enumerate(claims, start=1):
        v = by_id.get(n)
        # A claim the judge did not rule on is not assumed good.
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
