# Spec 08 — Six-scene demo (scripts/demo.py drives these via the API)

Each scene asserts machine-checkable outcomes and prints PASS/FAIL. `make demo` runs
all six in order against a freshly seeded database.

1. Onboarding. As a new visitor context, converse to collect name, email, lab, PI ack;
   agent returns approval_request; approve as the requester; assert an executed
   onboarding action and a new pending-access user row.
2. Availability + booking. As alice: "Is Confocal C2 free Thursday 2–4pm?" -> answer
   derived from bookings; then "book it on account CODE" -> approval -> executed
   booking with status 'requested'; assert the booking row and both audit entries.
3. Billing truth. As asha: "Why was lab A charged $412 in March?" -> rows_answer whose
   values exactly match the seeded v_billing_lines rows; assert the string "412.00"
   appears and every number in the reply exists in the returned rows.
4. Document -> service request. Upload the provided filled sample form (create a
   fixture PDF in scripts/fixtures/) as alice; agent extracts fields, drafts
   create_service_request; approve; assert the service_requests row matches extracted
   fields.
5. Per-user RAG proof. alice uploads private-note.md and asks about it -> answer with
   citation. bob asks the same question -> redirect, and assert bob's retrieval
   returned zero chunks from alice's doc (check via a test hook, not the reply text).
6. Verified or silent + ops. Ask an out-of-corpus policy question as alice -> redirect
   naming the closest doc or role. Then assert: the turn's trace exists (Langfuse or
   traces.jsonl), and the latest eval_runs row is present from `make eval`.

README-DEMO.md: the spoken 12-minute runbook mapping these scenes to talking points
(approval + audit = trust; scene 5 = privacy; scene 6 = never confidently wrong), plus
a pre-demo checklist (reseed, run make demo twice green, record a backup screen
capture).
