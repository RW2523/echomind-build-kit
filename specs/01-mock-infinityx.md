# Spec 01 — Mock Infinity X database

Purpose: a realistic stand-in for Infinity X so the demo is fully runnable. Tool
contracts are written so this backend can later be swapped for the real API or a read
replica by changing only the adapter layer.

## Tables (Postgres, schema `infinity`)

- labs(id, name, pi_user_id)
- users(id, email, name, role check in ('user','pi','admin'), lab_id, training jsonb,
  account_codes text[])
- facilities(id, name, code)
- instruments(id, facility_id, name, hourly_rate numeric, status)
- bookings(id, user_id, instrument_id, starts_at, ends_at, status check in
  ('requested','confirmed','cancelled','completed'), account_code)
- usage_records(id, instrument_id, user_id, booking_id nullable, starts_at, ends_at,
  source check in ('scheduled','tracked'))
- request_templates(id, facility_id, name, fields jsonb)  -- field defs incl. required
- service_requests(id, user_id, template_id, fields jsonb, status check in
  ('submitted','in_progress','completed','rejected'), history jsonb)
- samples(id, request_id, barcode, state, updated_at)
- projects(id, name, currency); project_members(project_id, user_id, role)
- invoices(id, account_code, period, total numeric);
  invoice_lines(id, invoice_id, description, instrument_id nullable, qty, unit_price, amount)
- maintenance_events(id, instrument_id, kind check in ('preventive','repair','alert'),
  notes, occurred_at, downtime_hours)

App schema `echomind`:
- knowledge_docs(id, title, version, visibility check in ('public','lab','private'),
  owner_user_id nullable, lab_id nullable, facility_id nullable, updated_at)
- chunks(id, doc_id, ord, text, breadcrumb, embedding vector, tsv tsvector,
  visibility, owner_user_id, lab_id, facility_id)  -- denormalized for filtering
- actions(id, user_id, tool, payload jsonb, status check in
  ('pending','approved','declined','executed','failed'), approver_id nullable,
  created_at, decided_at, executed_at, result jsonb)
- eval_runs(id, ran_at, metrics jsonb)

## Allow-listed reporting views (the ONLY relations run_readonly_sql may touch)

- v_bookings(user_id, user_name, lab_id, instrument, facility, starts_at, ends_at, status)
- v_usage_summary(lab_id, user_id, instrument, month, scheduled_hours, tracked_hours)
- v_billing_lines(account_code, lab_id, period, description, instrument, amount)
- v_instrument_downtime(instrument, facility, month, downtime_hours, repair_count)

## Roles

- echomind_app: full on echomind schema, read on infinity schema.
- echomind_readonly: SELECT on the four views only. run_readonly_sql connects as this
  role. Test that INSERT/UPDATE/DELETE fail for it.

## Seed volumes (deterministic, seeded RNG)

3 facilities, 12 instruments, 6 labs, 25 users, 200 bookings across the last 90 days,
500 usage records (mix scheduled/tracked, some tracked without booking), 8 request
templates, 40 service requests with samples, 4 projects, 3 monthly invoice periods with
lines that sum to invoice totals, 60 maintenance events.

Demo identities (fixed ids, JWTs minted by scripts/mint_jwt.py with HS256 + JWT_SECRET):
- alice — role user, lab A
- bob — role user, lab B
- asha — role pi, PI of lab A
- cora — role admin, all facilities

Seed one deliberate story for the demo: lab A charged exactly $412.00 in March for
"Confocal C2" usage, visible in v_billing_lines, so the billing scene has a precise,
verifiable answer.
