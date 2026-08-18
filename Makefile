.DEFAULT_GOAL := help
SHELL := /bin/bash

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/python -m pip
PYTEST  := $(VENV)/bin/pytest
UV      := $(shell command -v uv 2>/dev/null)

# Read only the ports out of .env (or .env.example). The application itself loads the
# full .env via pydantic-settings — deliberately NOT `include`d here, because make would
# export trailing inline comments (e.g. `RERANKER=none  # none | bge`) into the process
# environment, where they would override the correctly-parsed .env values.
API_PORT := $(shell grep -sh '^API_PORT=' .env .env.example | head -1 | cut -d= -f2 | tr -dc '0-9')
MCP_PORT := $(shell grep -sh '^MCP_PORT=' .env .env.example | head -1 | cut -d= -f2 | tr -dc '0-9')
ifeq ($(strip $(API_PORT)),)
API_PORT := 8080
endif
ifeq ($(strip $(MCP_PORT)),)
MCP_PORT := 8090
endif

.PHONY: help venv up down logs seed api mcp ui test eval demo convo journeys questions api-check ingest fmt clean

help:
	@echo "EchoMind Local — targets"
	@echo "  make up      docker compose: postgres (+ langfuse via COMPOSE_PROFILES=langfuse)"
	@echo "  make down    stop containers"
	@echo "  make seed    create schema, views, seed data, demo users, embed corpus"
	@echo "  make ingest  embed db/corpus into the chunks table"
	@echo "  make api     run FastAPI + MCP server (port $(API_PORT))"
	@echo "  make ui      run frontend dev server"
	@echo "  make test    pytest (markers: tools, rag_isolation, gate, sql_guard, tiers, agent)"
	@echo "  make eval    evals on evals/golden_set.jsonl -> eval_reports/"
	@echo "  make bench   score engines/models on the real tasks -> eval_reports/"
	@echo "  make demo    scripted six-scene run, prints PASS/FAIL per scene"

$(VENV)/.installed: pyproject.toml
ifeq ($(UV),)
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev,evals]"
else
	$(UV) venv --python 3.12 --allow-existing $(VENV)
	VIRTUAL_ENV=$(VENV) $(UV) pip install -q -e ".[dev,evals]"
endif
	@touch $@

venv: $(VENV)/.installed

# COMPOSE_PROFILES=langfuse make up  ->  also starts the optional Langfuse stack.
up:
	docker compose up -d
	@echo -n "waiting for postgres "
	@for i in $$(seq 1 60); do \
		if docker compose exec -T postgres pg_isready -U echomind -d echomind >/dev/null 2>&1; then \
			echo "ready"; exit 0; fi; \
		echo -n "."; sleep 1; \
	done; echo "TIMEOUT"; exit 1

down:
	docker compose --profile langfuse down

logs:
	docker compose logs -f --tail=100

# Seeding also embeds the corpus: every knowledge scene in the demo needs it, and a
# half-seeded database is a worse default than a slightly slower target.
seed: venv
	$(PY) -m scripts.seed
	@$(MAKE) --no-print-directory ingest

ingest: venv
	$(PY) -m server.rag.ingest db/corpus

api: venv
	$(PY) -m uvicorn server.main:app --host 0.0.0.0 --port $(API_PORT) --reload

mcp: venv
	$(PY) -m server.mcp.server

ui:
	cd ui && npm install && npm run dev

test: venv
	$(PYTEST) -q

eval: venv
	$(PY) -m evals.run

# Score candidate engines/models on EchoMind's own tasks. Endpoints that are not
# listening are skipped, so this works with whatever you happen to have running.
bench: venv
	$(PY) -m scripts.bench_llm --repeat 3

demo: venv
	$(PY) -m scripts.demo

# Whole conversations on one thread. Single-turn evals cannot see a turn that
# contradicts the one before it; this can. Starts its own API if none is running,
# and leaves one it did not start alone.
convo: venv
	$(PY) -m scripts.conversations

journeys: venv
	$(PY) -m scripts.journeys

questions: venv
	$(PY) -m scripts.questions

api-check: venv
	$(PY) -m scripts.api_check

fmt: venv
	$(VENV)/bin/ruff check --fix . && $(VENV)/bin/ruff format .

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

retrieval-eval:  ## measure retrieval on its own — recall@k, before an answer can hide it
	$(PY) -m scripts.retrieval_eval

scenarios:  ## drive the assistant through realistic situations; writes scenario_reports/
	$(PY) -m scripts.scenarios
