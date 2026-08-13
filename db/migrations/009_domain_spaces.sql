-- Domain data spaces.
--
-- `infinity` is the vendor's system of record. We do not own its layout and must not
-- reorganise it: the tools read it, the mock backend defines it, and a demo that quietly
-- rewrites someone else's schema is teaching the wrong lesson. So the segregation lives
-- where it can actually be enforced — in this application's own access layer.
--
-- Five spaces, split by what the data IS and how it changes, not by which table it
-- happens to sit in:
--
--   reference   labs, facilities, devices and what they cost. Slow-moving, maintained by
--               an admin or a lab expert. Reads are cheap and cacheable.
--   scheduling  who has what, when, and what is free. Changes constantly.
--   activity    what actually happened: usage against booking, downtime.
--   billing     what was charged. Append-only in practice, and the most sensitive.
--   policy      the company's and the application's own rules, as DATA rather than prose.
--
-- The first four are views over `infinity`; `policy` holds real tables, because those
-- rules are ours. Each space is granted separately, so a role can be given scheduling
-- without billing — which is the property that makes "segregated" mean something more
-- than a naming convention.

CREATE SCHEMA IF NOT EXISTS reference;
CREATE SCHEMA IF NOT EXISTS scheduling;
CREATE SCHEMA IF NOT EXISTS activity;
CREATE SCHEMA IF NOT EXISTS billing;
CREATE SCHEMA IF NOT EXISTS policy;

-- Dropped before creation, not CREATE OR REPLACE: replacing a view can only append
-- columns, so inserting one in the middle fails and the migration stops being
-- re-runnable. These are views over `infinity` and hold nothing of their own.
DROP VIEW IF EXISTS reference.v_labs, reference.v_facilities, reference.v_devices,
                    scheduling.v_bookings, scheduling.v_device_occupancy,
                    activity.v_usage, activity.v_downtime,
                    billing.v_charges CASCADE;

COMMENT ON SCHEMA reference  IS 'Slow-moving facts: labs, facilities, devices and rates. Admin-maintained.';
COMMENT ON SCHEMA scheduling IS 'Bookings and device occupancy: what is taken and what is free.';
COMMENT ON SCHEMA activity   IS 'What happened: usage against booking, and instrument downtime.';
COMMENT ON SCHEMA billing    IS 'What was charged, per account code and period.';
COMMENT ON SCHEMA policy     IS 'The rules themselves, as data — so a decision can cite the clause it applied.';


-- ---------------------------------------------------------------------------------
-- reference — the answer to "what exists, where is it, what does it cost"
-- ---------------------------------------------------------------------------------

CREATE VIEW reference.v_labs AS
SELECT l.id            AS lab_id,
       l.name          AS lab_name,
       l.pi_user_id,
       u.name          AS pi_name,
       u.email         AS pi_email,
       COUNT(DISTINCT m.id)  AS member_count,
       COUNT(DISTINCT a.code) AS account_code_count
FROM infinity.labs l
LEFT JOIN infinity.users u ON u.id = l.pi_user_id
LEFT JOIN infinity.users m ON m.lab_id = l.id
LEFT JOIN infinity.account_codes a ON a.lab_id = l.id
GROUP BY l.id, l.name, l.pi_user_id, u.name, u.email;

COMMENT ON VIEW reference.v_labs IS
  'One row per lab, with its PI and how many people and account codes it carries.';

CREATE VIEW reference.v_facilities AS
SELECT f.id      AS facility_id,
       f.name    AS facility_name,
       f.code,
       f.campus,
       f.building,
       f.room,
       f.address,
       f.latitude,
       f.longitude,
       f.contact_email,
       f.opening_hours,
       COUNT(i.id) AS instrument_count
FROM infinity.facilities f
LEFT JOIN infinity.instruments i ON i.facility_id = f.id
GROUP BY f.id, f.name, f.code, f.campus, f.building, f.room, f.address,
         f.latitude, f.longitude, f.contact_email, f.opening_hours;

COMMENT ON VIEW reference.v_facilities IS
  'Where each core is, how to reach it, and how many instruments it holds.';

-- The device catalogue and its price list in one place. Rate is per hour and comes from
-- the instrument row; the day and half-day figures beside it are derived, and named as
-- derived, so nobody reads them as a separate tariff the facility publishes.
CREATE VIEW reference.v_devices AS
SELECT i.id             AS instrument_id,
       i.name           AS instrument,
       i.status,
       i.modality,
       i.techniques,
       i.sample_types,
       i.specification,
       i.room,
       f.id             AS facility_id,
       f.name           AS facility,
       f.campus,
       f.building,
       i.hourly_rate,
       ROUND((i.hourly_rate * 4)::numeric, 2)  AS derived_half_day_rate,
       ROUND((i.hourly_rate * 8)::numeric, 2)  AS derived_day_rate
FROM infinity.instruments i
JOIN infinity.facilities f ON f.id = i.facility_id;

COMMENT ON VIEW reference.v_devices IS
  'Every device, where it lives, what it can do, and the hourly rate charged for it. '
  'The half-day and day figures are hourly_rate x 4 and x 8 — derived here, not a '
  'separate published tariff.';


-- ---------------------------------------------------------------------------------
-- scheduling — the answer to "when is it taken, and when is it free"
-- ---------------------------------------------------------------------------------

CREATE VIEW scheduling.v_bookings AS
SELECT b.id            AS booking_id,
       b.user_id,
       u.name          AS user_name,
       u.lab_id,
       b.instrument_id,
       i.name          AS instrument,
       f.id            AS facility_id,
       f.name          AS facility,
       b.starts_at,
       b.ends_at,
       b.status,
       b.account_code,
       ROUND((EXTRACT(EPOCH FROM (b.ends_at - b.starts_at)) / 3600)::numeric, 2) AS hours,
       -- Past, present or future relative to now. A booking is only "current" while the
       -- clock is inside it, which is the distinction a person asking "what do I have on"
       -- actually means.
       CASE
           WHEN b.ends_at   < now() THEN 'past'
           WHEN b.starts_at > now() THEN 'future'
           ELSE 'current'
       END AS when_relative
FROM infinity.bookings b
JOIN infinity.instruments i ON i.id = b.instrument_id
JOIN infinity.facilities  f ON f.id = i.facility_id
JOIN infinity.users       u ON u.id = b.user_id;

COMMENT ON VIEW scheduling.v_bookings IS
  'Every booking with its instrument, facility, duration, and whether it is past, '
  'current or future relative to now.';

-- Occupancy, per device per day: how much of the day is taken, and therefore what is
-- free.
--
-- A slot is occupied when a booking holds it, which means 'confirmed' (it will happen)
-- and 'completed' (it did). Written first as `status = 'confirmed'` alone, this counted
-- 1 of 202 bookings — every past session vanished and the view reported devices as free
-- on days they had been in use all afternoon. Telling someone a busy device is available
-- is the worst answer a scheduler can give, so both states count.
--
-- 'cancelled' frees the slot. 'requested' has not been approved yet, so it does not
-- occupy anything — but it is counted separately, because a scheduler looking at a day
-- needs to know a tentative hold exists before offering the same hour to someone else.
CREATE VIEW scheduling.v_device_occupancy AS
SELECT i.id                                   AS instrument_id,
       i.name                                 AS instrument,
       f.name                                 AS facility,
       i.status                               AS device_status,
       date_trunc('day', b.starts_at)::date   AS day,
       COUNT(*) FILTER (WHERE b.status IN ('confirmed', 'completed')) AS bookings,
       COUNT(*) FILTER (WHERE b.status = 'requested')                 AS pending_bookings,
       ROUND(COALESCE(SUM(EXTRACT(EPOCH FROM (b.ends_at - b.starts_at)) / 3600)
                      FILTER (WHERE b.status IN ('confirmed', 'completed')), 0)::numeric, 2)
                                              AS booked_hours,
       MIN(b.starts_at) FILTER (WHERE b.status IN ('confirmed', 'completed')) AS first_start,
       MAX(b.ends_at)   FILTER (WHERE b.status IN ('confirmed', 'completed')) AS last_end
FROM infinity.instruments i
JOIN infinity.facilities f ON f.id = i.facility_id
JOIN infinity.bookings   b ON b.instrument_id = i.id
                          AND b.status IN ('confirmed', 'completed', 'requested')
GROUP BY i.id, i.name, f.name, i.status, date_trunc('day', b.starts_at)::date;

COMMENT ON VIEW scheduling.v_device_occupancy IS
  'Booked hours per device per day. Confirmed and completed bookings occupy the slot; '
  'cancelled ones free it; requested ones are counted separately as tentative holds.';


-- ---------------------------------------------------------------------------------
-- activity — the answer to "what actually happened"
-- ---------------------------------------------------------------------------------

CREATE VIEW activity.v_usage AS
SELECT r.id            AS usage_id,
       r.user_id,
       u.name          AS user_name,
       u.lab_id,
       r.instrument_id,
       i.name          AS instrument,
       r.booking_id,
       r.starts_at,
       r.ends_at,
       r.source,
       to_char(r.starts_at, 'YYYY-MM') AS month,
       ROUND((EXTRACT(EPOCH FROM (r.ends_at - r.starts_at)) / 3600)::numeric, 2) AS hours
FROM infinity.usage_records r
JOIN infinity.instruments i ON i.id = r.instrument_id
JOIN infinity.users       u ON u.id = r.user_id;

COMMENT ON VIEW activity.v_usage IS
  'Tracked usage, per record, with the booking it belongs to where there is one.';

CREATE VIEW activity.v_downtime AS
SELECT m.id            AS event_id,
       m.instrument_id,
       i.name          AS instrument,
       f.name          AS facility,
       m.kind,
       m.notes,
       m.occurred_at,
       m.downtime_hours,
       to_char(m.occurred_at, 'YYYY-MM') AS month
FROM infinity.maintenance_events m
JOIN infinity.instruments i ON i.id = m.instrument_id
JOIN infinity.facilities  f ON f.id = i.facility_id;

COMMENT ON VIEW activity.v_downtime IS 'Maintenance events and the hours each cost.';


-- ---------------------------------------------------------------------------------
-- billing — the answer to "what was charged"
-- ---------------------------------------------------------------------------------

CREATE VIEW billing.v_charges AS
SELECT l.id            AS line_id,
       v.id            AS invoice_id,
       v.account_code,
       a.lab_id,
       v.period,
       l.description,
       l.instrument_id,
       i.name          AS instrument,
       l.qty,
       l.unit_price,
       l.amount
FROM infinity.invoice_lines l
JOIN infinity.invoices      v ON v.id = l.invoice_id
LEFT JOIN infinity.account_codes a ON a.code = v.account_code
LEFT JOIN infinity.instruments   i ON i.id = l.instrument_id;

COMMENT ON VIEW billing.v_charges IS
  'One row per charge line, with the account code, lab and period it belongs to.';


-- ---------------------------------------------------------------------------------
-- policy — the rules, as data
-- ---------------------------------------------------------------------------------
--
-- The prose lives in the knowledge corpus and is what a person should read. This table
-- is the same rules in the form a program can apply: a cancellation window is a number
-- of hours, and a decision that says "cancelled free of charge" has to be able to point
-- at the number it used rather than at a paragraph it hopes it understood.
--
-- source_doc_id ties each row back to the document it was taken from, so the two cannot
-- drift silently — the clause and the number are shown together at the moment they are
-- applied.

CREATE TABLE IF NOT EXISTS policy.statements (
    id              TEXT PRIMARY KEY,
    domain          TEXT NOT NULL,          -- booking | billing | access | data | safety
    subject         TEXT NOT NULL,          -- cancellation | reschedule | no_show | review
    title           TEXT NOT NULL,
    statement       TEXT NOT NULL,          -- the sentence a person is shown
    -- The applicable part, where there is one. NULL means "prose only": a rule that has
    -- no number is still a rule, and inventing a threshold to fill the column in would be
    -- exactly the fabrication this table exists to prevent.
    threshold_hours NUMERIC,
    charge_percent  NUMERIC,
    applies_to      TEXT NOT NULL DEFAULT 'all',
    source_doc_id   TEXT,                   -- echomind.knowledge_docs.id
    source_clause   TEXT,                   -- e.g. "§4.2"
    effective_from  DATE NOT NULL,
    version         TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS statements_domain_subject_idx
    ON policy.statements (domain, subject);

COMMENT ON TABLE policy.statements IS
  'Company and application rules in applicable form. Every row carries the document and '
  'clause it came from, so a decision can show the rule it applied.';


-- ---------------------------------------------------------------------------------
-- Grants — what makes these spaces separate rather than merely named
-- ---------------------------------------------------------------------------------
--
-- echomind_readonly is the role the agent's validated SQL runs as. It gets SELECT on the
-- domain views and on policy.statements, and nothing else — no base tables in `infinity`,
-- no write anywhere. Granting per schema is the point: scheduling can be handed out
-- without billing.

GRANT USAGE ON SCHEMA reference, scheduling, activity, billing, policy
    TO echomind_app, echomind_readonly;

GRANT SELECT ON ALL TABLES IN SCHEMA reference, scheduling, activity, billing
    TO echomind_app, echomind_readonly;

GRANT SELECT ON policy.statements TO echomind_app, echomind_readonly;
-- Only the application writes policy, and only through an audited admin path.
GRANT INSERT, UPDATE, DELETE ON policy.statements TO echomind_app;

-- Views created later in this schema inherit the same reads.
ALTER DEFAULT PRIVILEGES IN SCHEMA reference, scheduling, activity, billing
    GRANT SELECT ON TABLES TO echomind_app, echomind_readonly;
