-- 003_roles.sql — database roles. Enforcement point 2 of spec 05.
--
--   echomind_app       full on echomind, read on infinity
--   echomind_readonly  SELECT on the four reporting views and NOTHING else
--
-- Passwords are dev-only; the demo compose stack is not exposed off localhost.
-- Idempotent: safe to re-run.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'echomind_app') THEN
        CREATE ROLE echomind_app LOGIN PASSWORD 'echomind_app';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'echomind_readonly') THEN
        CREATE ROLE echomind_readonly LOGIN PASSWORD 'echomind_readonly';
    END IF;
END $$;

-- Start from zero for both roles so re-runs cannot accumulate privileges.
REVOKE ALL ON ALL TABLES    IN SCHEMA infinity, echomind, reporting FROM echomind_app, echomind_readonly;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA echomind                      FROM echomind_app, echomind_readonly;
REVOKE ALL ON SCHEMA infinity, echomind, reporting                  FROM echomind_app, echomind_readonly;

-- --- echomind_app -----------------------------------------------------------
GRANT USAGE ON SCHEMA echomind, infinity TO echomind_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA echomind TO echomind_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA echomind TO echomind_app;
GRANT SELECT ON ALL TABLES IN SCHEMA infinity TO echomind_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA echomind
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO echomind_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA echomind
    GRANT USAGE, SELECT ON SEQUENCES TO echomind_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA infinity GRANT SELECT ON TABLES TO echomind_app;

-- --- echomind_readonly ------------------------------------------------------
-- USAGE on `reporting` only. No USAGE on infinity/echomind means no privilege path to
-- any base table, regardless of what SQL slips past the validator.
GRANT USAGE ON SCHEMA reporting TO echomind_readonly;
GRANT SELECT ON reporting.v_bookings,
                reporting.v_usage_summary,
                reporting.v_billing_lines,
                reporting.v_instrument_downtime
    TO echomind_readonly;

-- Unqualified `FROM v_bookings` resolves for this role without exposing anything else.
ALTER ROLE echomind_readonly SET search_path = reporting;
ALTER ROLE echomind_readonly SET default_transaction_read_only = on;
ALTER ROLE echomind_readonly SET statement_timeout = '5s';

-- Deny the ambient rights every role inherits from PUBLIC.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE echomind FROM PUBLIC;
GRANT CONNECT ON DATABASE echomind TO echomind_app, echomind_readonly;
