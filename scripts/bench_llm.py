"""Benchmark inference engines and models on EchoMind's own tasks.

    python -m scripts.bench_llm                 # all configured candidates
    python -m scripts.bench_llm --only trtllm   # substring filter
    python -m scripts.bench_llm --repeat 3

Tokens per second is the wrong question. What decides whether this application works is
whether the model routes correctly, returns a complete verdict set, locates the right
source, and cites what it claims — so the benchmark drives the real router, the real
gate, the real faithfulness checker and the real generator, and reports accuracy
alongside latency.

Retrieval and embeddings are held constant (Ollama/bge-m3) so the only variable is the
chat model and the engine serving it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

warnings.filterwarnings("ignore")

from server.config import REPO_ROOT, settings  # noqa: E402

REPORTS = REPO_ROOT / "eval_reports"

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"

QWEN3_NO_THINK = '{"chat_template_kwargs": {"enable_thinking": false}}'


@dataclass
class Candidate:
    name: str
    base_url: str
    model: str
    engine: str
    extra_body: str = ""
    note: str = ""


CANDIDATES = [
    Candidate("ollama/qwen2.5-7b-q4", "http://localhost:11434/v1", "qwen2.5:7b-instruct",
              "Ollama (llama.cpp)", note="current default"),
    Candidate("trtllm/qwen3-8b-fp4", "http://localhost:8001/v1", "nvidia/Qwen3-8B-FP4",
              "TensorRT-LLM", extra_body=QWEN3_NO_THINK, note="NVFP4, Blackwell native"),
    Candidate("trtllm/llama3.1-8b-fp4", "http://localhost:8003/v1",
              "nvidia/Llama-3.1-8B-Instruct-FP4", "TensorRT-LLM", note="NVFP4"),
    Candidate("vllm/qwen3-8b-fp4", "http://localhost:8000/v1", "nvidia/Qwen3-8B-FP4",
              "vLLM", extra_body=QWEN3_NO_THINK),
]

# --- task fixtures ------------------------------------------------------------------

ROUTER_CASES = [
    ("How long do the confocal lasers need to warm up?", "knowledge"),
    ("When are invoices issued?", "knowledge"),
    ("What is the maximum length of a single booking?", "knowledge"),
    ("How long is Biosafety Level 2 valid for?", "knowledge"),
    ("What format do sample barcodes use?", "knowledge"),
    ("Show me my bookings", "data"),
    ("Why was lab A charged $412 in March?", "data"),
    ("How many bookings do I have in total?", "data"),
    ("Which instrument had the most downtime in March 2026?", "data"),
    ("Book Confocal C2 tomorrow at 9am on ACC-A1", "action"),
    ("Please submit my RNA-seq form as a service request", "action"),
    ("Generate the monthly summary for 2026-03", "action"),
    ("Who won the 2019 Nobel prize in physics?", "out_of_scope"),
    ("Write me a Python script to sort a list", "out_of_scope"),
]

SOURCE_SOP = (
    "The Confocal C2 is a point-scanning confocal microscope in the Advanced Imaging Core, "
    "charged at $42.00 per hour at the internal rate. Start-up must be performed in order: "
    "switch on the power strip, then the controller, then the laser key. Allow the lasers to "
    "warm up for 30 minutes before acquiring any quantitative data. The 30-minute warm-up is "
    "not optional for quantitative imaging: intensity drift over the first 20-25 minutes is "
    "routinely 5-8%. Shutdown reverses this, with a 10-minute laser cool-down before power off."
)
SOURCE_BILLING = (
    "Invoices are issued monthly, in arrears, in the first week of the following month. Each "
    "invoice covers one account code for one calendar month, and its total is the sum of its "
    "lines. Charges can be disputed within 60 days of the invoice date. Internal rates are "
    "reviewed annually each July; external users pay 1.8x the internal rate."
)
SOURCE_TRAINING = (
    "Biosafety Level 2 certification is valid for 24 months and is refreshed with a half-day "
    "in-person session. Confocal Basics is valid for 24 months. Cryo-EM Operation is valid for "
    "12 months and requires a full day. A certification lapsed by less than 60 days can be "
    "renewed with the refresher alone; beyond 60 days the full initial training repeats."
)

# (question, sources, expected source index or 0 for "not covered")
COVERAGE_CASES = [
    ("How long must the confocal lasers warm up?", [SOURCE_SOP, SOURCE_BILLING], 1),
    ("When are invoices issued?", [SOURCE_SOP, SOURCE_BILLING], 2),
    ("How long is Biosafety Level 2 valid?", [SOURCE_BILLING, SOURCE_TRAINING], 2),
    ("What is the parking permit policy for visitors?", [SOURCE_SOP, SOURCE_BILLING], 0),
    ("How do I book the seminar room?", [SOURCE_TRAINING, SOURCE_BILLING], 0),
    ("What is the cool-down time after imaging?", [SOURCE_SOP, SOURCE_TRAINING], 1),
]

GENERATION_CASES = [
    ("How long must the confocal lasers warm up before quantitative imaging?",
     [SOURCE_SOP], "30"),
    ("When are invoices issued?", [SOURCE_BILLING], "first week"),
    ("How long is Biosafety Level 2 certification valid for?", [SOURCE_TRAINING], "24"),
    ("What is the hourly rate for the Confocal C2?", [SOURCE_SOP], "42"),
]

# Claim sets of increasing size — the shape that broke the judge before.
VERDICT_CASES = [
    ("The lasers warm up for 30 minutes [1].", [SOURCE_SOP], 1),
    ("The lasers warm up for 30 minutes [1]. Intensity drift is 5-8% over 20-25 minutes [1].",
     [SOURCE_SOP], 2),
    ("The Confocal C2 costs $42.00 per hour [1]. It is a point-scanning confocal [1]. "
     "The lasers need 30 minutes of warm-up [1]. Shutdown needs a 10-minute cool-down [1].",
     [SOURCE_SOP], 4),
]


@dataclass
class TaskScore:
    correct: int = 0
    total: int = 0
    latencies: list[float] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else float("nan")

    @property
    def p50(self) -> float:
        return statistics.median(self.latencies) if self.latencies else float("nan")


def _chunk(text: str, idx: int):
    from server.rag.retrieval import RetrievedChunk

    return RetrievedChunk(
        chunk_id=idx, doc_id=f"doc-{idx}", text=text, breadcrumb=f"Source {idx} (v1)",
        score=0.8, rrf=0.5, vector_rank=idx, fts_rank=idx, title=f"Source {idx}",
        visibility="public",
    )


def point_at(candidate: Candidate) -> None:
    """Repoint the real code paths at this endpoint. Only the chat model changes."""
    import server.agent.llm as llm_mod

    settings.llm_base_url = candidate.base_url
    settings.llm_model = candidate.model
    settings.judge_model = candidate.model
    settings.llm_extra_body = candidate.extra_body
    llm_mod._client = None
    llm_mod._schema_style = None


def reachable(candidate: Candidate) -> bool:
    for path in ("/models", ""):
        try:
            r = httpx.get(candidate.base_url.rstrip("/") + path, timeout=4)
            if r.status_code < 500:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


# --- individual task runners ----------------------------------------------------------


def bench_router(repeat: int) -> TaskScore:
    from server.agent.router import route

    s = TaskScore()
    for question, expected in ROUTER_CASES:
        for _ in range(repeat):
            t = time.time()
            try:
                got, _why = route(question)
            except Exception as exc:  # noqa: BLE001
                got = f"error:{type(exc).__name__}"
            s.latencies.append(time.time() - t)
            s.total += 1
            if got == expected:
                s.correct += 1
            else:
                s.failures.append(f"{question[:42]} -> {got} (want {expected})")
    return s


def bench_coverage(repeat: int) -> TaskScore:
    from server.agent.gate import _coverage_check

    s = TaskScore()
    for question, sources, expected in COVERAGE_CASES:
        chunks = [_chunk(t, i) for i, t in enumerate(sources, start=1)]
        for _ in range(repeat):
            t = time.time()
            try:
                covered = _coverage_check(question, chunks)
            except Exception:  # noqa: BLE001
                covered = False
            s.latencies.append(time.time() - t)
            s.total += 1
            if covered == (expected > 0):
                s.correct += 1
            else:
                s.failures.append(f"{question[:42]} -> covered={covered} (want {expected > 0})")
    return s


def bench_verdicts(repeat: int) -> TaskScore:
    """Does the judge return a verdict for EVERY claim? This is the reliability that
    decides whether correct answers survive."""
    from server.agent import faithfulness as faith

    s = TaskScore()
    for answer, sources, n_claims in VERDICT_CASES:
        chunks = [_chunk(t, i) for i, t in enumerate(sources, start=1)]
        for _ in range(repeat):
            t = time.time()
            try:
                result = faith.check(answer, chunks, [])
                complete = all(v.why != "judge returned no verdict" for v in result.verdicts)
                right_count = result.checked == n_claims
                ok = complete and right_count and result.passed
            except Exception as exc:  # noqa: BLE001
                ok, complete, right_count = False, False, False
                s.failures.append(f"{n_claims} claims -> {type(exc).__name__}")
            s.latencies.append(time.time() - t)
            s.total += 1
            if ok:
                s.correct += 1
            elif complete and right_count:
                s.failures.append(f"{n_claims} claims -> judged but marked unsupported")
            else:
                s.failures.append(f"{n_claims} claims -> incomplete verdict set")
    return s


def bench_generation(repeat: int) -> TaskScore:
    from server.agent import generate as gen

    s = TaskScore()
    for question, sources, must_contain in GENERATION_CASES:
        chunks = [_chunk(t, i) for i, t in enumerate(sources, start=1)]
        for _ in range(repeat):
            t = time.time()
            try:
                text, cites, sufficient = gen.generate(question, chunks)
            except Exception:  # noqa: BLE001
                text, cites, sufficient = "", [], False
            s.latencies.append(time.time() - t)
            s.total += 1
            if sufficient and cites and must_contain.lower() in text.lower():
                s.correct += 1
            elif not sufficient:
                s.failures.append(f"{question[:40]} -> declined")
            elif not cites:
                s.failures.append(f"{question[:40]} -> no citation")
            else:
                s.failures.append(f"{question[:40]} -> missing '{must_contain}'")
    return s


def bench_terseness(repeat: int) -> TaskScore:
    """Instruction-following on an exact-output request. A model that pads every answer
    with commentary makes every downstream parse and every reply worse."""
    from server.agent.llm import chat

    s = TaskScore()
    for _ in range(repeat * 3):
        t = time.time()
        try:
            out = chat([{"role": "user", "content": "Reply with exactly the word: ready"}],
                       max_tokens=30, temperature=0.0)
        except Exception:  # noqa: BLE001
            out = ""
        s.latencies.append(time.time() - t)
        s.total += 1
        if out.strip().lower().rstrip(".") == "ready":
            s.correct += 1
        else:
            s.failures.append(repr(out[:60]))
    return s


TASKS = {
    "router": bench_router,
    "coverage": bench_coverage,
    "verdicts": bench_verdicts,
    "generation": bench_generation,
    "terseness": bench_terseness,
}

# Weights reflect what breaks the product when it fails, not how interesting it is.
WEIGHTS = {"router": 0.2, "coverage": 0.2, "verdicts": 0.3, "generation": 0.25,
           "terseness": 0.05}


def run_candidate(candidate: Candidate, repeat: int) -> dict[str, Any]:
    point_at(candidate)
    from server.agent.llm import structured_output_mode

    scores: dict[str, TaskScore] = {}
    for name, fn in TASKS.items():
        print(f"    {name:11} ", end="", flush=True)
        score = fn(repeat)
        scores[name] = score
        colour = GREEN if score.accuracy >= 0.9 else (RED if score.accuracy < 0.7 else "")
        print(f"{colour}{score.accuracy:6.1%}{RESET}  p50 {score.p50:5.2f}s"
              f"   {DIM}{score.correct}/{score.total}{RESET}")

    weighted = sum(WEIGHTS[k] * (s.accuracy if s.accuracy == s.accuracy else 0)
                   for k, s in scores.items())
    total_p50 = sum(s.p50 for s in scores.values() if s.p50 == s.p50)
    return {
        "candidate": candidate.name,
        "engine": candidate.engine,
        "model": candidate.model,
        "note": candidate.note,
        "structured_output": structured_output_mode(),
        "weighted_score": round(weighted, 4),
        "tasks": {
            k: {"accuracy": round(s.accuracy, 4), "p50_s": round(s.p50, 3),
                "correct": s.correct, "total": s.total, "failures": s.failures[:6]}
            for k, s in scores.items()
        },
        "sum_p50_s": round(total_p50, 3),
    }


def render(results: list[dict[str, Any]], repeat: int) -> str:
    lines = [
        f"# LLM engine / model benchmark — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC",
        "",
        f"Repeats per case: {repeat}. Retrieval and embeddings held constant (bge-m3 via "
        "Ollama); only the chat model and its serving engine vary.",
        "",
        "Scored on EchoMind's own tasks rather than throughput. Weights: verdicts 0.30, "
        "generation 0.25, router 0.20, coverage 0.20, terseness 0.05 — a model that cannot "
        "return a complete verdict set silently suppresses correct answers, which is the "
        "most expensive failure this system has.",
        "",
        "| Candidate | Engine | Structured output | Score | Router | Coverage | Verdicts | Generation | Terse | Σ p50 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(results, key=lambda x: -x["weighted_score"]):
        t = r["tasks"]
        lines.append(
            f"| `{r['candidate']}` | {r['engine']} | {r['structured_output']} "
            f"| **{r['weighted_score']:.3f}** "
            f"| {t['router']['accuracy']:.0%} | {t['coverage']['accuracy']:.0%} "
            f"| {t['verdicts']['accuracy']:.0%} | {t['generation']['accuracy']:.0%} "
            f"| {t['terseness']['accuracy']:.0%} | {r['sum_p50_s']:.2f}s |"
        )
    lines += ["", "## Latency per task (p50, seconds)", "",
              "| Candidate | Router | Coverage | Verdicts | Generation | Terse |",
              "|---|---:|---:|---:|---:|---:|"]
    for r in sorted(results, key=lambda x: -x["weighted_score"]):
        t = r["tasks"]
        lines.append(
            f"| `{r['candidate']}` | {t['router']['p50_s']:.2f} | {t['coverage']['p50_s']:.2f} "
            f"| {t['verdicts']['p50_s']:.2f} | {t['generation']['p50_s']:.2f} "
            f"| {t['terseness']['p50_s']:.2f} |"
        )

    lines += ["", "## Where each candidate failed", ""]
    for r in sorted(results, key=lambda x: -x["weighted_score"]):
        lines.append(f"### `{r['candidate']}` — {r['model']}")
        lines.append("")
        any_fail = False
        for task, t in r["tasks"].items():
            if t["failures"]:
                any_fail = True
                lines.append(f"- **{task}** ({t['correct']}/{t['total']})")
                for f in t["failures"]:
                    lines.append(f"  - {f}")
        if not any_fail:
            lines.append("- no failures")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="substring filter on candidate name")
    ap.add_argument("--repeat", type=int, default=2)
    args = ap.parse_args()

    chosen = [c for c in CANDIDATES if args.only in c.name]
    print(f"{BOLD}EchoMind LLM benchmark{RESET}  ({args.repeat} repeat(s) per case)")

    results = []
    for candidate in chosen:
        if not reachable(candidate):
            print(f"\n  {DIM}skip {candidate.name} — {candidate.base_url} unreachable{RESET}")
            continue
        print(f"\n  {BOLD}{candidate.name}{RESET}  {DIM}{candidate.engine} · "
              f"{candidate.model}{RESET}")
        results.append(run_candidate(candidate, args.repeat))

    if not results:
        print("no reachable candidates")
        return 2

    report = render(results, args.repeat)
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    (REPORTS / f"bench-{stamp}.md").write_text(report, encoding="utf-8")
    (REPORTS / f"bench-{stamp}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"\n{BOLD}{'=' * 70}{RESET}")
    for r in sorted(results, key=lambda x: -x["weighted_score"]):
        print(f"  {r['weighted_score']:.3f}  {r['candidate']:26} "
              f"{DIM}Σp50 {r['sum_p50_s']:5.2f}s · structured={r['structured_output']}{RESET}")
    best = max(results, key=lambda x: x["weighted_score"])
    print(f"\n{GREEN}{BOLD}winner: {best['candidate']}{RESET} "
          f"({best['engine']}, score {best['weighted_score']:.3f})")
    print(f"report: eval_reports/bench-{stamp}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
