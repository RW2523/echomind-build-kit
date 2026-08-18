# Running EchoMind Local

The configuration below is not a preference. Every value in it was chosen from a
measurement in this repository, and the measurement is named so you can re-run it and
disagree.

## The setup to run

| Component | Value | Why this one |
|---|---|---|
| Chat + judge model | `nvidia/Qwen3-8B-FP4` on TensorRT-LLM, `:8001` | Scores **1.000** on EchoMind's own task benchmark, and serves 8 concurrent callers at **243 tok/s, 1.03s p50** — six times Ollama's throughput and a third of its latency under load. |
| Embeddings | `bge-m3` via Ollama, `:11434`, 1024-dim | Held constant across every benchmark; retrieval numbers in `eval_reports/` are all measured against it. |
| Reranker | `bge` cross-encoder, `:8006` | Measured lift on the corpus: context precision **0.805 → 0.866**. |
| Database | Postgres 16 + pgvector, `:5432` | Records, chunks, actions, audit and graph checkpoints in one place. |
| Escalation | `ESCALATION_ENABLED=false` | Golden rule 6. No cloud model is reachable from the core path. |

`eval_reports/bench-2026-08-10.md` has the full table. Two things in it are worth knowing
before you swap anything:

- **`ollama/qwen2.5-7b-q4` also scores 1.000** and is *faster for a single request*
  (3.29s vs 4.44s p50). It is the better choice on a laptop and the wrong one for a
  facility, because at eight concurrent callers it drops to 41 tok/s and 3.46s p50.
- **`llama3.1-8b-fp4` scores 0.886 and should not be used.** It returned a complete
  verdict set only 6 times in 9. An incomplete verdict set does not look like a failure —
  it silently suppresses correct answers, which is the most expensive thing that can go
  wrong here.

## Bringing it up

```bash
cp .env.example .env      # then set the values in the table above
make up                   # postgres (+ langfuse if COMPOSE_PROFILES=langfuse)
make seed                 # schema, views, nine months of demo data, demo users
make ingest               # embed db/corpus into the chunks table
make api                  # FastAPI on :8080
make ui                   # React on :5173
```

`make mcp` additionally exposes the same 20 tools over MCP on `:8090` for external
clients. The agent does not use it — it calls the tools in-process — so it is optional.

## Checking it works

Six suites, each asking something the others cannot see. All six pass on this build.

```bash
make test        # 952 unit and integration tests
make eval        # 20 golden questions; enforced gates must be 1.000
make demo        # six scripted scenes, PASS/FAIL each
make convo       # 22 conversations — what a turn SAYS
make journeys    # 48 turns — that a journey COMPLETES (writes to the demo db)
make questions   # 54 turns — that a reply is HONEST when asked badly
make api-check   # 74 checks across all 34 endpoints, including every refusal
```

`make eval` is the one to watch. Two of its five numbers are enforced and the run fails if
either drops: **data exact-match** and **redirect/forbidden**, both 1.000. The three
knowledge metrics are reported. Read them as a trend and expect movement of a few points
between runs — but do not wave a step change away as noise. When correctness fell from
0.980 to 0.729 on one question it was traceable to a single prompt change, not to the
grader.

## Demo posture vs shared posture

`DEMO_LOGIN_ENABLED=true` is what makes the demo work: one click signs you in as Alice,
Bob, Asha or Cora. It is also the whole authentication story, so **on any URL other people
can reach, the link is the credential** — including as `cora`, who is an admin.

Two settings, one decision:

```bash
DEMO_LOGIN_ENABLED=false      # anyone reaching the URL must present a real JWT
JWT_SECRET=<32+ random bytes> # the dev default is refused at startup
```

`scripts/mint_jwt.py` issues tokens for a real deployment. Nothing else about the product
changes — permissions are read from verified claims either way, in the tool layer and in
the retrieval filter.

Still dev posture, and each is a decision rather than an oversight: no rate limiting, a
single uvicorn worker with `--reload`, and HS256 with a shared secret rather than an
asymmetric key or your IdP.

## Resetting the demo

`make journeys` books and cancels real records, and anyone clicking through the UI moves
the numbers too.

```bash
make seed        # back to 5 cores, 19 instruments, 620 bookings, nine invoice periods
```

The seed prints `lab-a Mar Confocal C2 $412.00` when it finishes. That is the demo's
verifiable number, asserted to the penny by `test_march_billing_story_is_exact`, and it is
the quickest confirmation that the data is what the tests expect.
