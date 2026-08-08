# Spec 06 — Config, observability, evals

## .env.example (complete list)

    DATABASE_URL=postgresql://echomind:echomind@localhost:5432/echomind
    JWT_SECRET=dev-only-change-me
    LLM_BASE_URL=http://localhost:11434/v1
    LLM_MODEL=qwen2.5:7b-instruct
    JUDGE_MODEL=qwen2.5:7b-instruct
    EMBED_BASE_URL=http://localhost:11434/v1
    EMBED_MODEL=bge-m3
    RERANKER=none            # none | bge
    GATE_MIN_TOP_SCORE=0.45
    ESCALATION_ENABLED=false
    FRONTIER_BASE_URL=
    LANGFUSE_ENABLED=false
    LANGFUSE_HOST=http://localhost:3000
    LANGFUSE_PUBLIC_KEY=
    LANGFUSE_SECRET_KEY=
    API_PORT=8080
    MCP_PORT=8090

Dev profile: Ollama with qwen2.5:7b-instruct and bge-m3 pulled. Spark profile: point
LLM_BASE_URL at vLLM/TensorRT-LLM serving the 70B, JUDGE_MODEL to the 70B, RERANKER=bge.
Code must not branch on profile — env only.

## Tracing

Wrap every graph node and tool call in Langfuse spans (trace per chat turn, named
route/tool). When LANGFUSE_ENABLED=false, a console tracer logs the same structure as
JSON lines to logs/traces.jsonl — the demo admin page reads the latest entries from
either source. Tag spans: route, gate_result, sql_valid, action_kind, escalated=false.

## Golden set

evals/golden_set.jsonl — 20 entries: {id, user (alice|bob|asha|cora), question,
expected_answer, expected_sources[], kind (knowledge|data|redirect|forbidden)}.
At least: 8 knowledge grounded in db/corpus, 6 data grounded in seed rows (including
the $412 March line), 3 redirect (out-of-corpus), 3 permission (must refuse).

## RAGAS runner (make eval)

For knowledge items: faithfulness, answer_correctness, context_precision using
JUDGE_MODEL via the OpenAI-compatible endpoint. For data items: exact-match of the
numeric values against seed truth. For redirect/forbidden items: assert response_type.
Write eval_reports/<date>.md with per-item results and averages, and insert an
eval_runs row. Thresholds (prod targets, report-only under the dev model):
faithfulness >= 0.90, answer_correctness >= 0.85, data exact-match = 100%,
redirect/forbidden = 100%. `make eval` exits nonzero if the last two are not 100%.
