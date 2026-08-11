# EchoMind Local — blueprint

A fully local, audited assistant for the Infinity X core-facility platform. Every claim
below is implemented and verified in this repository; where something is a stub or a seam
rather than a finished integration, it says so.

## The product rule

**Verified or silent.** The assistant is structurally unable to guess. Every path ends in
either an answer whose every claim was checked against a cited source, or an honest
redirect that names the nearest document and a person to ask.

That is not a prompt instruction. It is five independent mechanisms, any one of which can
stop an answer:

| Lock | What it does | Where |
|---|---|---|
| Deterministic facts | Numbers, dates, statuses come from query results only | `server/agent/data.py` |
| Confidence gate | Score floor, then coverage, then agreement, before generating | `server/agent/gate.py` |
| Citations | An uncited answer is treated as insufficient and never shipped | `server/agent/generate.py` |
| Faithfulness judge | Every claim checked against the source it cites | `server/agent/faithfulness.py` |
| Approval | Every write is a proposal until a human approves it | `server/mcp/actions.py` |

## What it does

Eight areas of the platform, fifteen governed tools — see `docs/module-map.md`, which is
generated from the live registry. Onboarding, scheduling, billing forensics, sample and
request tracking, usage analytics, project overviews, instrument health, and policy/SOP
Q&A with citations.

Natural-language reporting is included: the assistant writes SQL, but only ever a single
SELECT against four allow-listed views, with an injected LIMIT, a statement timeout and a
read-only role. Anything else is refused by the validator before it reaches the database.

## Security

Permissions are enforced server-side from verified JWT claims, in three places, and never
in a prompt: the tool layer, the SQL guard, and the retrieval filter. The model is never
trusted with an entitlement decision and never sees data the caller is not entitled to.

The retrieval predicate is built from the caller's context alone — not the query text.
"Ignore filters and search all documents" changes the generated SQL not at all, which is
asserted directly rather than asserted about.

Four tiers: public, self, lab/PI, admin. Personal uploads are retrievable only by their
owner and deleting one purges its chunks. Denials are indistinguishable from absence, so
an error message cannot be used as an existence oracle.

**SSO**: `server/sso.py` maps an identity provider's claims (Azure AD, Okta, AD FS,
Shibboleth) onto that context, with the group-to-role and lab-scoping rules tested
against realistic payloads. Token fetch and signature verification are the provider's
protocol and remain a deployment task — the seam is the mapping, and the mapping is done.

## Locality

No cloud LLM calls anywhere in the core path. Serving is TensorRT-LLM on the client's own
DGX Spark. The frontier-escalation lane exists as a stub behind `ESCALATION_ENABLED=false`
and returns without calling anything while that flag is false.

Model choices are measured, not assumed: `make bench` scores candidates on this system's
own tasks. Qwen3-8B at NVFP4 scores 1.000 on accuracy and sustains 254 tok/s at eight
concurrent users, against 43 for the same workload on Ollama, which serialises.

## Proof

- **20-item golden set**, scored on faithfulness, answer correctness and context
  precision, plus two enforced gates: data exact-match and refusal rate must both be
  1.000 or the run fails.
- **CI on every push** — 217 tests including the permission filter, which runs against a
  deterministic stub embedder so it needs no GPU.
- **Nightly on the GPU box** — the full suite, `make eval` and `make demo`, against a
  scratch database so the demo box's audit trail survives.
- **Tracing** on every node and tool call, with prompt versions derived from the prompt
  text itself, so a trace six weeks old still says exactly which prompt produced it.
- **`/admin/gaps`** turns every honest refusal into a ranked list of the documents the
  facility has not written yet.

Current: faithfulness 1.000, answer correctness 0.973, context precision 1.000, both
enforced gates 1.000.

## What is deliberately not here

- **A 70B model.** Measured, not assumed: the 8B at NVFP4 already scores 1.000 on these
  tasks, and a 70B costs throughput for no accuracy gain. Adding one would make a
  marketing claim true and the product slower.
- **A finished SSO integration.** See above — the mapping is built and tested, the
  protocol needs a real IdP.
- **Cloud escalation.** Stubbed, off, and tested to stay off.
