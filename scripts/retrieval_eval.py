"""Measure retrieval on its own, before an answer can hide what it did.

Every other number in this repo is end-to-end: faithfulness, correctness, precision. That
makes a retrieval fault visible only when it changes a final answer on a golden question,
and it is perfectly possible for retrieval to be badly wrong while all three look healthy.

It happened. A five-section policy document was ingested as one 360-token chunk — session
limits, fair-share caps, cancellation charges, instrument status and bumping averaged into
a single embedding that resembled no question in particular. "What is the cancellation
policy" scored 0.55 against the document that answers it directly, most of the corpus fell
below the confidence floor, and the correct answer was refused for want of context.
Corpus-wide context_precision read 0.933 throughout, because precision only judges what
was retrieved — never what was missed.

So this asks one question per line of evals/retrieval_set.jsonl and checks whether the
fact is in the top k, with no generator involved:

    recall@k   did any retrieved chunk contain the fact?
    MRR        how far down the list was it?
    floor      would the confidence gate have kept it?

A miss here is a fact the assistant cannot reach, whatever the answer layer does with it.

    make retrieval-eval
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.config import REPO_ROOT
from server.mcp.actions import _ctx_for
from server.rag.retrieval import retrieve

SET = REPO_ROOT / "evals" / "retrieval_set.jsonl"
OUT_DIR = REPO_ROOT / "eval_reports"

# The gate's cosine floor. A chunk below it is retrieved and then discarded, so a fact that
# only appears under the floor is a fact the answer layer never sees.
FLOOR = 0.45


@dataclass
class Result:
    question: str
    topic: str
    must_contain: str
    rank: int | None          # 1-based position of the first chunk carrying the fact
    score: float | None       # its score, so a near-miss on the floor is visible
    breadcrumb: str | None
    retrieved: int

    @property
    def found(self) -> bool:
        return self.rank is not None

    @property
    def above_floor(self) -> bool:
        return self.score is not None and self.score >= FLOOR


def _first_carrying(chunks, needle) -> tuple[int | None, float | None, str | None]:
    """Where the fact first appears, if it appears at all.

    Substring rather than a judge: whether "24 hours" is in a passage is not a matter of
    opinion, and a retrieval metric that depends on an LLM inherits its bad days.

    A list of spellings counts as one fact. The corpus writes "fourteen days" in prose and
    "14 days" in a table, and the first run of this file reported a MISS for a passage it
    had ranked first — a false alarm in the instrument rather than a fault in the system,
    which is exactly the failure that makes people stop trusting a metric.
    """
    wanted = [needle] if isinstance(needle, str) else list(needle)
    lowered = [w.lower() for w in wanted]
    for position, chunk in enumerate(chunks, start=1):
        haystack = chunk.text.lower()
        if any(w in haystack for w in lowered):
            return position, chunk.score, chunk.breadcrumb
    return None, None, None


def run(k: int, who: str) -> list[Result]:
    ctx = _ctx_for(who)
    results: list[Result] = []
    for line in SET.read_text().splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        chunks = retrieve(case["question"], ctx, k=k)
        rank, score, crumb = _first_carrying(chunks, case["must_contain"])
        results.append(Result(
            question=case["question"], topic=case.get("topic", ""),
            must_contain=case["must_contain"], rank=rank, score=score,
            breadcrumb=crumb, retrieved=len(chunks),
        ))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--user", default="u-alice",
                        help="whose permissions to retrieve under")
    parser.add_argument("--min-recall", type=float, default=0.8,
                        help="fail below this recall@k")
    args = parser.parse_args()

    results = run(args.k, args.user)
    total = len(results)
    found = [r for r in results if r.found]
    usable = [r for r in found if r.above_floor]

    def recall_at(n: int) -> float:
        return round(sum(1 for r in results if r.rank and r.rank <= n) / total, 3)

    mrr = round(sum(1 / r.rank for r in found) / total, 3) if total else 0.0
    report = {
        "k": args.k,
        "questions": total,
        "recall@1": recall_at(1),
        "recall@3": recall_at(3),
        f"recall@{args.k}": recall_at(args.k),
        "mrr": mrr,
        "above_floor": round(len(usable) / total, 3),
        "misses": [
            {"question": r.question, "topic": r.topic, "wanted": r.must_contain,
             "retrieved": r.retrieved}
            for r in results if not r.found
        ],
        "below_floor": [
            {"question": r.question, "score": r.score, "breadcrumb": r.breadcrumb}
            for r in found if not r.above_floor
        ],
    }
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "retrieval.json").write_text(json.dumps(report, indent=2))

    print()
    for r in results:
        if not r.found:
            spelled = (r.must_contain if isinstance(r.must_contain, str)
                       else " / ".join(r.must_contain))
            mark, detail = "MISS", f"'{spelled}' in none of {r.retrieved} chunks"
        elif not r.above_floor:
            mark, detail = "WEAK", f"rank {r.rank}, score {r.score:.3f} — under the {FLOOR} floor"
        else:
            mark, detail = "ok  ", f"rank {r.rank}, score {r.score:.3f}  {r.breadcrumb[:48]}"
        print(f"  {mark}  {r.question[:56]:58} {detail}")

    print(f"\n  recall@1 {report['recall@1']}   recall@3 {report['recall@3']}   "
          f"recall@{args.k} {report[f'recall@{args.k}']}   MRR {mrr}   "
          f"above floor {report['above_floor']}")
    print(f"  report: {OUT_DIR / 'retrieval.json'}")

    if report[f"recall@{args.k}"] < args.min_recall:
        print(f"\n  FAIL: recall@{args.k} below {args.min_recall}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
