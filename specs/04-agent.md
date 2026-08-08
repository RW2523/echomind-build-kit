# Spec 04 — Agent graph (LangGraph)

State: {messages, ctx (auth claims), route, retrieved, gate, draft, citations, rows,
pending_action_id, response_type in ('answer','redirect','approval_request','scope',
'rows_answer')}. Checkpointer: Postgres. One graph instance serves the chat endpoint;
thread_id = conversation id.

## 1. Router

LLM classification into: knowledge | data | action | smalltalk | out_of_scope.
Scope = Infinity X world only (facility ops, onboarding, scheduling, samples, billing,
projects, instruments, policies, documents). smalltalk gets a brief friendly reply.
out_of_scope gets the fixed scope message naming what the assistant does cover.
Ambiguous data-vs-knowledge defaults to data when the question asks for a number,
status, date, or "my/our" records.

## 2. Knowledge branch

retrieve(query, ctx) -> gate -> generate -> faithfulness.
Gate (env-tunable): GATE_MIN_TOP_SCORE (default 0.45 with reranker off), coverage check
(a cheap LLM yes/no: "do these chunks contain the answer?"), agreement check only when
chunks conflict. Fail -> redirect response: state what is not verified and name the
closest doc breadcrumb or the right role to ask (admin for billing, PI for lab docs).
Generate: answer ONLY from chunks; every sentence with a factual claim carries a
citation index; low temperature. Faithfulness: judge model verifies each claim is
supported by its cited chunk; any unsupported claim -> redirect instead.

## 3. Data branch

sql_plan (LLM writes SELECT against the four views, schema provided in prompt) ->
validator (spec 02) -> execute -> answer_from_rows: the reply template interpolates
values ONLY from returned rows; include a compact rows table in the response payload so
the UI can render evidence. Zero rows -> say so plainly and suggest the nearest valid
question. Validator rejection -> one silent repair attempt with the error, then a
redirect. Simple T1 lookups (my bookings, my requests) should call tools 4/6/8
directly instead of SQL.

## 4. Action branch

Build exact payload via tools 12–15 -> respond with approval_request (payload preview)
-> graph interrupts. POST /actions/{id}/approve resumes the thread: execute, then
confirm in chat with the result reference. Decline resumes with a polite cancellation.
The confirmation message values come from the action result row.

## 5. Escalation stub

Node exists; if ESCALATION_ENABLED=false (default) it is never routed to. If enabled,
it would pseudonymize names/codes and call FRONTIER_BASE_URL — implement the
pseudonymizer and the routing condition (gate score in a borderline band), but the demo
ships with the flag off and a unit test proving no egress when off.
