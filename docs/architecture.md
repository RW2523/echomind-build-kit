# EchoMind architecture

Drawn from the code, not from the plan: every node, tool count and enforcement point
below is what `server/` actually contains. If a diagram and the code disagree the diagram
is worse than nothing, so the shapes here are deliberately the ones you can go and read.

## The turn

```mermaid
flowchart TD
    U([User message]) --> AUTH{{"JWT verified<br/>server/auth.py"}}
    AUTH -->|"Ctx: sub, role, lab_ids, facility_ids"| ROUTE[route]

    ROUTE -->|knowledge| KN[knowledge]
    ROUTE -->|data| DA[data]
    ROUTE -->|action| AP[action_propose]
    ROUTE -->|smalltalk| SM[smalltalk]
    ROUTE -->|out of scope| OS[out_of_scope]

    subgraph K ["knowledge branch — verified or silent"]
        KN --> RW["rewrite<br/><i>resolve the follow-up</i>"]
        RW --> RET["hybrid retrieval<br/><i>vector + FTS, RRF, rerank</i>"]
        RET --> GATE{"confidence gate<br/><i>score / coverage / agreement</i>"}
        GATE -->|fails| RED[["honest redirect"]]
        GATE -->|passes| GEN["grounded generation<br/><i>cite every claim</i>"]
        GEN --> FAITH{"faithfulness judge<br/><i>every claim vs its source</i>"}
        FAITH -->|unsupported| RED
        FAITH -->|supported| ANS[["cited answer"]]
    end

    subgraph D ["data branch — facts only from queries"]
        DA --> PLAN["plan: tool or SQL"]
        PLAN --> GUARD{"SQL guard<br/><i>single SELECT, allow-listed views,<br/>enforced LIMIT, read-only role</i>"}
        GUARD -->|rejected| RED
        GUARD -->|validated| ROWS[["rows answer + evidence"]]
    end

    subgraph A ["action branch — nothing happens without approval"]
        AP --> PEND["pending action<br/><i>payload shown in full</i>"]
        PEND --> WAIT{{"interrupt()<br/>durable checkpoint"}}
        WAIT -->|approved| EXEC["execute + audit"]
        WAIT -->|declined| DECL["audit only"]
    end

    RED --> GAPS[("knowledge_gaps<br/><i>what to write next</i>")]
    EXEC --> MEM[("user_memory<br/><i>preferences only</i>")]
    EXEC --> AUD[("audit_log")]
    DECL --> AUD
```

## Where permissions are enforced

Never in a prompt. Three places, all server-side, all from the verified JWT context:

```mermaid
flowchart LR
    CTX["Ctx from verified claims<br/>server/auth.py"]
    CTX --> T["1. Tool layer<br/><i>tier check before any query</i><br/>server/mcp/tools.py"]
    CTX --> S["2. SQL guard<br/><i>lab-scoped rewrite, read-only role</i><br/>server/mcp/sql_guard.py"]
    CTX --> R["3. Retrieval filter<br/><i>predicate built from ctx alone</i><br/>server/rag/retrieval.py"]
    T --> DB[(Postgres)]
    S --> DB
    R --> DB
```

The retrieval predicate is built from `ctx` and nothing else — not the query text, not the
model, not the request body. A prompt saying "ignore filters and search all documents"
changes the SQL not at all, which is asserted directly in `tests/test_rag_isolation.py`.

## Deployment

```mermaid
flowchart TB
    subgraph BOX ["The client's building — no cloud LLM calls"]
        UI["React + Vite<br/>SSE streaming"] --> API["FastAPI<br/>+ FastMCP (15 tools)"]
        API --> LG["LangGraph<br/>Postgres checkpointer"]
        API --> PG[(Postgres 16 + pgvector<br/><i>app · chunks · audit · checkpoints</i>)]
        LG --> LLM["TensorRT-LLM<br/>Qwen3-8B-FP4"]
        API --> EMB["bge-m3 embeddings"]
        API --> RR["Qwen3-Reranker-4B"]
    end
    ESC["frontier escalation"] -.->|"stub, ESCALATION_ENABLED=false"| API
```

Three database roles: the owner runs migrations and seeding, `echomind_app` serves
requests (read on the platform, insert on exactly three tables, no DDL), and
`echomind_readonly` is what every generated SQL statement executes as.

## The five locks on an answer

1. **Deterministic facts** — numbers, dates and statuses come from query results only.
2. **Confidence gate** — score floor, then coverage, then agreement, before generating.
3. **Faithfulness judge** — every claim checked against the source it cites; one
   unsupported claim downgrades the whole answer to a redirect.
4. **Citations** — an uncited answer is treated as insufficient, not shipped.
5. **Approval** — every write is a proposal until a human approves it, and every
   proposal, approval and decline is audited.
