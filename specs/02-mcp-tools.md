# Spec 02 — The 15-tool MCP server

FastMCP server mounted alongside FastAPI. Every call requires a verified JWT; a tool
context object {user_id, role, lab_ids, facility_ids} is derived server-side and passed
to every handler. Tier checks happen in the handler BEFORE any query.

Tiers: T0 any authenticated user; T1 self only (param user_id must equal caller unless
role in ('pi','admin') within scope); T2 pi for own lab(s); T3 admin.

## Read tools

| # | Tool | Params | Returns | Tier |
|---|------|--------|---------|------|
| 1 | get_user_profile | user_id | profile incl. roles, lab, training, account_codes | T1 self / T2 lab / T3 |
| 2 | get_facility_catalog | facility_id? | facilities, instruments, rates, templates | T0 |
| 3 | check_availability | instrument_id, date_from, date_to | free slots (derived from bookings) | T0 |
| 4 | get_my_bookings | date_from?, date_to? | caller's bookings | T1 |
| 5 | get_usage_records | scope(user|lab|instrument), id, month | scheduled vs tracked hours | T1/T2 |
| 6 | get_request_status | request_id? or mine=true | request(s) with status + history | T1/T2 |
| 7 | track_sample | barcode or sample_id | sample state timeline | T1/T2 (owner's lab) |
| 8 | get_billing_summary | account_code, period | invoice total + lines | T1 (own codes) / T2 / T3 |
| 9 | get_project_overview | project_id | members, cores, spend | T2 member/pi / T3 |
| 10 | get_instrument_health | instrument_id | status T0; history+downtime T3 |
| 11 | run_readonly_sql | sql | rows (max 200) + executed_sql | T2/T3 |

## Write tools (never execute directly)

| # | Tool | Params | Creates pending action of kind |
|---|------|--------|-------------------------------|
| 12 | create_onboarding_request | name, email, lab_id, pi_ack, account_code? | onboarding |
| 13 | create_service_request | template_id, fields | service_request |
| 14 | request_booking | instrument_id, starts_at, ends_at, account_code | booking |
| 15 | generate_document | template in ('usage_report','onboarding_packet','monthly_summary'), params | document |

Write flow: tool validates params against the template/schema, checks tier, inserts
actions row status='pending', returns {action_id, payload_preview, status}. Nothing
touches infinity.* yet. POST /actions/{id}/approve (caller must be the requesting user
for T1 kinds; admin may approve any) transitions pending->approved, executes the
mapped mutation (or renders the document to files/outputs/), records result and
status='executed' (or 'failed' with error). /decline sets declined. All transitions
timestamped. generate_document output files are registered in the action result.

## SQL guard (tool 11)

- Parse with sqlglot; reject on parse error, multiple statements, or any node type
  other than a plain SELECT (no DDL/DML/CTE-with-writes/functions like pg_sleep).
- Every referenced relation must be in {v_bookings, v_usage_summary, v_billing_lines,
  v_instrument_downtime}; otherwise reject with the allow-list in the error.
- Inject LIMIT 200 if absent; statement_timeout 5s; execute as echomind_readonly.
- For role 'pi', append/AND a lab_id filter to the caller's labs (rewrite via sqlglot);
  admins are unrestricted across the views.
- Log {caller, sql_in, sql_executed, row_count} on every call.

## Errors

Uniform error object {code, message, hint}. Tier denial is code 'forbidden' and must
not reveal whether the target resource exists.
