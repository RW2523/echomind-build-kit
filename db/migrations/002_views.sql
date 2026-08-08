-- 002_views.sql — the four allow-listed reporting views.
--
-- These live in their own schema so the read-only role can be granted USAGE on
-- `reporting` and nothing else: even if the SQL validator were bypassed entirely, the
-- role has no privilege path to the `infinity` or `echomind` base tables. The views are
-- owned by the migration role and are NOT security_invoker, so they read base tables
-- with the owner's rights.

CREATE OR REPLACE VIEW reporting.v_bookings AS
SELECT b.user_id,
       u.name        AS user_name,
       u.lab_id,
       i.name        AS instrument,
       f.name        AS facility,
       b.starts_at,
       b.ends_at,
       b.status
FROM infinity.bookings b
JOIN infinity.users u       ON u.id = b.user_id
JOIN infinity.instruments i ON i.id = b.instrument_id
JOIN infinity.facilities f  ON f.id = i.facility_id;

CREATE OR REPLACE VIEW reporting.v_usage_summary AS
SELECT u.lab_id,
       ur.user_id,
       i.name AS instrument,
       to_char(date_trunc('month', ur.starts_at), 'YYYY-MM') AS month,
       round(sum(CASE WHEN ur.source = 'scheduled'
                      THEN extract(epoch FROM (ur.ends_at - ur.starts_at)) / 3600
                      ELSE 0 END)::numeric, 2) AS scheduled_hours,
       round(sum(CASE WHEN ur.source = 'tracked'
                      THEN extract(epoch FROM (ur.ends_at - ur.starts_at)) / 3600
                      ELSE 0 END)::numeric, 2) AS tracked_hours
FROM infinity.usage_records ur
JOIN infinity.users u       ON u.id = ur.user_id
JOIN infinity.instruments i ON i.id = ur.instrument_id
GROUP BY u.lab_id, ur.user_id, i.name, date_trunc('month', ur.starts_at);

CREATE OR REPLACE VIEW reporting.v_billing_lines AS
SELECT inv.account_code,
       ac.lab_id,
       inv.period,
       il.description,
       ins.name AS instrument,
       il.amount
FROM infinity.invoice_lines il
JOIN infinity.invoices inv        ON inv.id = il.invoice_id
JOIN infinity.account_codes ac    ON ac.code = inv.account_code
LEFT JOIN infinity.instruments ins ON ins.id = il.instrument_id;

CREATE OR REPLACE VIEW reporting.v_instrument_downtime AS
SELECT i.name AS instrument,
       f.name AS facility,
       to_char(date_trunc('month', m.occurred_at), 'YYYY-MM') AS month,
       round(sum(m.downtime_hours)::numeric, 2) AS downtime_hours,
       count(*) FILTER (WHERE m.kind = 'repair')::int AS repair_count
FROM infinity.maintenance_events m
JOIN infinity.instruments i ON i.id = m.instrument_id
JOIN infinity.facilities f  ON f.id = i.facility_id
GROUP BY i.name, f.name, date_trunc('month', m.occurred_at);
