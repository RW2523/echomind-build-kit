# Build plan — work top to bottom, verify before advancing

Each milestone lists: spec to read, tasks, and verification. A milestone is done only
when every verification passes. Log decisions in DECISIONS.md as you go.

## M0 — Scaffold and infrastructure
Read: CLAUDE.md only.
Tasks: repo layout from CLAUDE.md; docker-compose (postgres:16 with pgvector,
optional Langfuse services); Makefile with all targets; .env.example with every
variable in specs/06; health endpoint GET /healthz.
Verify: `make up` then `curl localhost:8080/healthz` returns {"ok":true} after `make api`.

## M1 — Mock Infinity X database
Read: specs/01-mock-infinityx.md.
Tasks: schema migrations, allow-listed reporting views, read-only DB role, seed script
with the stated volumes, demo users alice/bob/asha/cora, scripts/mint_jwt.py.
Verify: `make seed`; `pytest -m seed_counts` asserts row counts and that the read-only
role cannot INSERT.

## M2 — The 15-tool MCP server
Read: specs/02-mcp-tools.md and specs/05-access-control.md.
Tasks: implement all 15 tools with tier enforcement from JWT claims; SQL validator;
pending-action flow for the 4 write tools; audit records; POST /actions/{id}/approve
and /decline.
Verify: `pytest -m tools` (happy paths), `pytest -m sql_guard` (validator rejects
non-SELECT, unknown views, missing LIMIT injection), `pytest -m tiers` (bob is denied
alice's data; asha sees lab A only; cora sees all).

## M3 — Ingestion and permission-filtered retrieval
Read: specs/03-rag.md.
Tasks: ingestion CLI for markdown/PDF -> chunks with metadata + embeddings; hybrid
retrieval (vector + Postgres FTS, RRF merge); mandatory permission filter built from
JWT; optional reranker behind env flag; sample corpus in db/corpus/ (write ~10 short
facility SOP/policy docs yourself, varied visibility).
Verify: `pytest -m rag_isolation` — the same query as alice vs bob returns disjoint
private chunks; bob can never retrieve alice's private doc; public chunks appear for both.

## M4 — Gate, grounded generation, faithfulness
Read: specs/04-agent.md sections 1–3.
Tasks: confidence gate (score floor, coverage, agreement; thresholds via env);
grounded generation with citations; faithfulness pass; honest-redirect response type.
Verify: `pytest -m gate` — an in-corpus question yields a cited answer; an
out-of-corpus question yields a redirect, not a guess; a tampered context test makes
faithfulness downgrade the answer.

## M5 — LangGraph agent with approvals
Read: specs/04-agent.md in full.
Tasks: router (knowledge / data / action / out-of-scope); data branch via SQL tool with
answer-from-rows; action branch producing pending actions and resuming after approval
(Postgres checkpointer); scope refusal message; escalation stub.
Verify: `pytest -m agent` — end-to-end: a billing question returns values matching
seeded rows exactly; a booking request creates a pending action, approval executes it,
audit shows both events; an out-of-scope prompt gets the scope message.

## M6 — Observability and evals
Read: specs/06-observability-evals.md.
Tasks: Langfuse tracing on every node and tool call (console fallback when disabled);
evals/golden_set.jsonl with 20 questions grounded in the seed data and corpus;
RAGAS runner writing eval_reports/<date>.md.
Verify: `make eval` produces a report with faithfulness, answer correctness, and
context precision per question plus averages; traces visible (or console-logged).

## M7 — Chat UI
Read: specs/07-ui.md.
Tasks: React chat with SSE streaming, login-as switcher (4 demo users), file upload to
personal RAG, citation chips, approval cards, redirect styling, admin page (audit table,
latest eval scores, Langfuse link).
Verify: manual checklist in specs/07 completed; `make demo` still green.

## M8 — Demo runbook
Read: specs/08-demo.md.
Tasks: scripts/demo.py driving all six scenes through the real API; README-DEMO.md
with the spoken runbook.
Verify: `make demo` prints PASS for all six scenes twice in a row.
