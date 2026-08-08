# EchoMind Local — project memory

You are building EchoMind Local: a fully local, audited AI assistant for the Infinity X
core-facility platform, running as a demo against a mock Infinity X backend.
The product rule everywhere is **verified or silent**.

## Golden rules (never violate)

1. Facts (numbers, dates, statuses, balances) in any answer come ONLY from tool results.
   The LLM composes sentences; it never invents values.
2. Knowledge answers must pass the confidence gate AND the faithfulness check, and must
   carry citations. If either fails, return the honest redirect — never a guess.
3. All SQL goes through the validator: single SELECT, allow-listed views only,
   enforced LIMIT and timeout, read-only DB role. No exceptions, including tests.
4. Every write tool returns a pending action. Execution happens only after explicit
   approval via the API. Every action (approved or declined) lands in the audit table.
5. Permissions are enforced server-side from verified JWT claims — in the tool layer and
   in the retrieval filter. Never in prompts. The model must never see data the caller
   is not entitled to.
6. No cloud LLM calls anywhere in the core path. The escalation tool exists as a stub
   behind ESCALATION_ENABLED=false.
7. Do not store Infinity X data beyond action records and knowledge chunks. Live data is
   fetched per request from the mock backend, answered from, and discarded.

## Stack (pinned)

- Python 3.11+, FastAPI, FastMCP (MCP server), LangGraph (+ Postgres checkpointer),
  SQLAlchemy/psycopg, sqlglot (SQL validation), pytest
- Postgres 16 + pgvector (single database: app, chunks, audit, checkpoints)
- Models via OpenAI-compatible endpoints, configured only through env vars
  (dev profile = Ollama on localhost; prod profile = vLLM/TensorRT-LLM on DGX Spark)
- Langfuse (self-hosted, optional via LANGFUSE_ENABLED) + RAGAS for evals
- Frontend: React + Vite + TypeScript, SSE streaming

## Repo layout (target)

    server/          FastAPI app: auth, chat, actions, admin
    server/mcp/      the 15-tool MCP server
    server/agent/    LangGraph graph, gate, faithfulness
    server/rag/      ingestion, retrieval, permission filter
    db/              migrations, seed, views
    ui/              React app
    evals/           golden_set.jsonl, RAGAS runner
    scripts/         demo.py, seed.py, mint_jwt.py
    specs/           the specs you must follow

## Commands

    make up      # docker compose: postgres, langfuse (optional)
    make seed    # create schema, views, seed data, demo users
    make api     # run FastAPI + MCP server
    make ui      # run frontend dev server
    make test    # pytest (markers: tools, rag_isolation, gate, sql_guard)
    make eval    # RAGAS on evals/golden_set.jsonl -> eval_reports/
    make demo    # scripted six-scene run, prints PASS/FAIL per scene

## How to work

- Follow PLAN.md milestones strictly in order. Before starting a milestone, read the
  spec file it names in full.
- After each milestone, run its verification. Do not continue while anything is red.
- Make sensible choices without asking; record every non-obvious choice in DECISIONS.md
  (one line: date, decision, why).
- Keep secrets out of git. All config via .env (copy .env.example).
- Every milestone that adds behavior adds tests for that behavior.
