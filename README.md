# EchoMind build kit — how to use with Claude Code

This folder is a complete instruction set for Claude Code to build the EchoMind Local
demo end to end: mock Infinity X backend, 15-tool MCP server, permission-filtered RAG,
LangGraph agent with approvals, Langfuse + RAGAS, and the chat UI.

## What's here

    CLAUDE.md      project memory — Claude Code loads this automatically every session
    PLAN.md        ordered milestones M0–M8 with verification commands
    DECISIONS.md   empty log Claude fills in as it makes choices
    specs/         eight detailed specs, one per subsystem

Why this shape: Claude Code auto-loads a project-root CLAUDE.md at session start, and
short memory files with pointers work better than one giant file, so the rules live in
CLAUDE.md and the detail lives in specs/ that Claude reads per milestone. (Skills are
for reusable cross-project workflows; a one-project build is better served by project
memory + specs.)

## Prerequisites on the build machine

- Docker + Docker Compose, Python 3.11+, Node 18+
- Ollama running with models pulled: `ollama pull qwen2.5:7b-instruct` and
  `ollama pull bge-m3` (dev profile — no DGX Spark needed to build and demo)
- Claude Code installed: https://docs.claude.com/en/docs/claude-code/overview

On the DGX Spark later, only .env changes: point LLM_BASE_URL at your vLLM/TensorRT-LLM
70B endpoint and set RERANKER=bge. No code changes.

## Run it

1. Copy this folder's contents into an empty git repo (files at the repo root,
   specs/ preserved).
2. Start Claude Code in that folder.
3. Paste this kickoff prompt:

   > Read CLAUDE.md, PLAN.md, and DECISIONS.md. Build this project by working through
   > PLAN.md milestones strictly in order, starting at M0. Before each milestone, read
   > the spec file it names in full. After each milestone, run its verification
   > commands and fix failures before moving on — never advance while red. Make
   > sensible choices without asking me and log each one in DECISIONS.md. Finish
   > through M8, then run `make demo` twice and show me both outputs.

4. When it finishes: `make up && make seed && make api && make ui`, open the UI,
   and walk the six scenes from specs/08-demo.md.

## If a run stalls

Tell Claude Code: "Re-read PLAN.md, find the first milestone whose verification fails,
and resume from there." The milestone verifications are the ground truth of progress.
