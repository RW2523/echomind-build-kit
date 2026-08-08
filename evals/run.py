"""RAGAS runner — `make eval`.

Per spec 06:
  knowledge items   faithfulness, answer_correctness, context_precision — the RAGAS
                    metric definitions, judged by JUDGE_MODEL over the
                    OpenAI-compatible endpoint (see evals/metrics.py)
  data items        exact-match of the numeric values against seed truth
  redirect/forbidden assert the response_type

Writes eval_reports/<date>.md and inserts an eval_runs row. Exits non-zero if data
exact-match or redirect/forbidden are not 100% — the LLM-judged metrics are report-only
under the dev model, as the spec says.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from server.agent.graph import run_turn
from server.auth import Ctx
from server.config import REPO_ROOT, settings
from server.db import session_scope
from server.demo_identities import DEMO_USERS
from evals.metrics import score_all

warnings.filterwarnings("ignore")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("echomind").setLevel(logging.WARNING)

GOLDEN_SET = REPO_ROOT / "evals" / "golden_set.jsonl"
REPORTS_DIR = REPO_ROOT / "eval_reports"

# Prod targets. Report-only for the LLM-judged metrics under the 7B dev judge.
THRESHOLDS = {
    "faithfulness": 0.90,
    "answer_correctness": 0.85,
    "data_exact_match": 1.0,
    "redirect_forbidden": 1.0,
}

REFUSAL_TYPES = {"redirect", "scope"}


@dataclass
class ItemResult:
    id: str
    kind: str
    user: str
    question: str
    response_type: str
    answer: str
    passed: bool
    detail: str = ""
    metrics: dict[str, float] = field(default_factory=dict)
    seconds: float = 0.0


def _ctx(handle: str) -> Ctx:
    u = DEMO_USERS[handle]
    return Ctx(
        user_id=u["id"], name=u["name"], role=u["role"],
        lab_ids=tuple(u["lab_ids"]), facility_ids=tuple(u["facility_ids"]),
    )


def load_golden_set() -> list[dict[str, Any]]:
    items = []
    for line in GOLDEN_SET.read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(json.loads(line))
    return items


# --- metric wiring --------------------------------------------------------------------


def _score_knowledge(item: dict, response) -> dict[str, float]:
    """faithfulness / answer_correctness / context_precision for one knowledge item."""
    contexts = [f"{c.breadcrumb}\n{_chunk_text(c)}" for c in response.citations]
    if not contexts:
        contexts = [""]
    try:
        scores = score_all(
            answer=response.text,
            contexts=contexts,
            reference=item["expected_answer"],
            question=item["question"],
        )
    except Exception as exc:  # noqa: BLE001 — a judging failure must not abort the run
        print(f"    metrics failed: {type(exc).__name__}: {str(exc)[:120]}")
        return {}
    return {
        "faithfulness": scores.faithfulness,
        "answer_correctness": scores.answer_correctness,
        "context_precision": scores.context_precision,
    }


def _chunk_text(citation) -> str:
    """Fetch the cited chunk's text through the one permitted read path."""
    from server.rag.retrieval import chunk_text_by_id

    return chunk_text_by_id(citation.chunk_id) or ""


# --- per-kind evaluation --------------------------------------------------------------


def evaluate_item(item: dict) -> ItemResult:
    ctx = _ctx(item["user"])
    started = time.time()
    response = run_turn(item["question"], ctx, f"eval-{item['id']}-{int(started)}")
    elapsed = time.time() - started

    result = ItemResult(
        id=item["id"], kind=item["kind"], user=item["user"], question=item["question"],
        response_type=response.response_type, answer=response.text, passed=False,
        seconds=round(elapsed, 1),
    )

    if item["kind"] == "knowledge":
        if response.response_type != "answer":
            result.detail = f"expected an answer, got {response.response_type}"
        elif not response.citations:
            result.detail = "answer carried no citations"
        else:
            result.passed = True
            result.metrics = _score_knowledge(item, response)

    elif item["kind"] == "data":
        expected = item.get("expected_values", [])
        missing = [v for v in expected if v not in response.text]
        if response.response_type != "rows_answer":
            result.detail = f"expected rows_answer, got {response.response_type}"
        elif missing:
            result.detail = f"missing exact value(s): {', '.join(missing)}"
        else:
            result.passed = True
        result.metrics = {"exact_match": 0.0 if missing else 1.0}

    elif item["kind"] in ("redirect", "forbidden"):
        if response.response_type in REFUSAL_TYPES:
            result.passed = True
        else:
            result.detail = f"expected a refusal, got {response.response_type}"
        result.metrics = {"refused": 1.0 if result.passed else 0.0}

    return result


# --- report ---------------------------------------------------------------------------


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if v == v]  # drop NaN
    return round(sum(clean) / len(clean), 3) if clean else None


def build_report(results: list[ItemResult], started: datetime) -> tuple[str, dict]:
    by_kind: dict[str, list[ItemResult]] = {}
    for r in results:
        by_kind.setdefault(r.kind, []).append(r)

    knowledge = by_kind.get("knowledge", [])
    averages = {
        name: _mean([r.metrics.get(name, float("nan")) for r in knowledge])
        for name in ("faithfulness", "answer_correctness", "context_precision")
    }
    data_items = by_kind.get("data", [])
    refusal_items = by_kind.get("redirect", []) + by_kind.get("forbidden", [])

    data_exact = _mean([r.metrics.get("exact_match", 0.0) for r in data_items]) or 0.0
    refusal_rate = _mean([r.metrics.get("refused", 0.0) for r in refusal_items]) or 0.0

    summary = {
        "ran_at": started.isoformat(timespec="seconds"),
        "model": settings.llm_model,
        "judge_model": settings.judge_model,
        "embed_model": settings.embed_model,
        "reranker": settings.reranker,
        "items": len(results),
        "passed": sum(1 for r in results if r.passed),
        "faithfulness": averages["faithfulness"],
        "answer_correctness": averages["answer_correctness"],
        "context_precision": averages["context_precision"],
        "data_exact_match": data_exact,
        "redirect_forbidden": refusal_rate,
    }

    def fmt(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.3f}"

    lines = [
        f"# EchoMind eval report — {started.strftime('%Y-%m-%d %H:%M')} UTC",
        "",
        f"- Model: `{settings.llm_model}`  ",
        f"- Judge: `{settings.judge_model}`  ",
        f"- Embeddings: `{settings.embed_model}`, reranker `{settings.reranker}`  ",
        f"- Items: {summary['items']}, passed {summary['passed']}",
        "",
        "## Averages",
        "",
        "| Metric | Value | Prod target | Gate |",
        "|---|---:|---:|---|",
        f"| faithfulness (knowledge) | {fmt(averages['faithfulness'])} | "
        f"{THRESHOLDS['faithfulness']:.2f} | report-only |",
        f"| answer_correctness (knowledge) | {fmt(averages['answer_correctness'])} | "
        f"{THRESHOLDS['answer_correctness']:.2f} | report-only |",
        f"| context_precision (knowledge) | {fmt(averages['context_precision'])} | — | report-only |",
        f"| data exact-match | {fmt(data_exact)} | 1.000 | **enforced** |",
        f"| redirect/forbidden | {fmt(refusal_rate)} | 1.000 | **enforced** |",
        "",
        "## Per item",
        "",
        "| id | kind | user | result | faith | corr | ctx-prec | s | note |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.id} | {r.kind} | {r.user} | {'PASS' if r.passed else 'FAIL'} "
            f"| {fmt(r.metrics.get('faithfulness'))} "
            f"| {fmt(r.metrics.get('answer_correctness'))} "
            f"| {fmt(r.metrics.get('context_precision'))} "
            f"| {r.seconds} | {r.detail or r.response_type} |"
        )

    lines += ["", "## Questions and answers", ""]
    for r in results:
        lines += [
            f"### {r.id} — {r.kind} (as {r.user})",
            "",
            f"**Q:** {r.question}",
            "",
            f"**A** (`{r.response_type}`): {r.answer.strip() or '_empty_'}",
            "",
        ]
    return "\n".join(lines) + "\n", summary


def main() -> int:
    started = datetime.now(UTC)
    items = load_golden_set()
    print(f"eval: {len(items)} golden items against {settings.llm_model}")

    results: list[ItemResult] = []
    for item in items:
        result = evaluate_item(item)
        results.append(result)
        flag = "PASS" if result.passed else "FAIL"
        extra = f" {result.metrics}" if result.metrics else ""
        print(f"  [{flag}] {result.id} {result.kind:9} {result.seconds:5.1f}s "
              f"{result.detail or result.response_type}{extra}")

    report, summary = build_report(results, started)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{started.strftime('%Y-%m-%d')}.md"
    path.write_text(report, encoding="utf-8")

    with session_scope() as s:
        s.execute(
            text("INSERT INTO echomind.eval_runs (ran_at, metrics) VALUES (:t, CAST(:m AS jsonb))"),
            {"t": started, "m": json.dumps(summary)},
        )

    print(f"\nreport: {path.relative_to(REPO_ROOT)}")
    print(f"  faithfulness       {summary['faithfulness']}")
    print(f"  answer_correctness {summary['answer_correctness']}")
    print(f"  context_precision  {summary['context_precision']}")
    print(f"  data exact-match   {summary['data_exact_match']}  (must be 1.0)")
    print(f"  redirect/forbidden {summary['redirect_forbidden']}  (must be 1.0)")

    hard_failures = []
    if summary["data_exact_match"] < THRESHOLDS["data_exact_match"]:
        hard_failures.append("data exact-match below 100%")
    if summary["redirect_forbidden"] < THRESHOLDS["redirect_forbidden"]:
        hard_failures.append("redirect/forbidden below 100%")

    if hard_failures:
        print("\nFAIL: " + "; ".join(hard_failures))
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
