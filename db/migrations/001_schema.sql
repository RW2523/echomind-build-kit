-- 001_schema.sql — Mock Infinity X backend (schema `infinity`) + EchoMind app (`echomind`).
-- Idempotent: safe to re-run.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS infinity;
CREATE SCHEMA IF NOT EXISTS echomind;
CREATE SCHEMA IF NOT EXISTS reporting;

-- ---------------------------------------------------------------------------
-- infinity — the mock core-facility platform
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS infinity.labs (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    pi_user_id  text                      -- FK added after users exists
);

CREATE TABLE IF NOT EXISTS infinity.users (
    id            text PRIMARY KEY,
    email         text UNIQUE NOT NULL,
    name          text NOT NULL,
    role          text NOT NULL CHECK (role IN ('user', 'pi', 'admin')),
    lab_id        text REFERENCES infinity.labs (id),
    training      jsonb NOT NULL DEFAULT '{}'::jsonb,
    account_codes text[] NOT NULL DEFAULT '{}'
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'labs_pi_user_id_fkey') THEN
        ALTER TABLE infinity.labs
            ADD CONSTRAINT labs_pi_user_id_fkey
            FOREIGN KEY (pi_user_id) REFERENCES infinity.users (id) DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS infinity.facilities (
    id   text PRIMARY KEY,
    name text NOT NULL,
    code text UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS infinity.instruments (
    id          text PRIMARY KEY,
    facility_id text NOT NULL REFERENCES infinity.facilities (id),
    name        text NOT NULL,
    hourly_rate numeric(10, 2) NOT NULL,
    status      text NOT NULL CHECK (status IN ('available', 'maintenance', 'offline'))
);

-- Account codes are lab-scoped billing identifiers. Not in the spec's table list, but
-- v_billing_lines must expose lab_id, and deriving it by scanning users.account_codes[]
-- would be both slow and ambiguous.
CREATE TABLE IF NOT EXISTS infinity.account_codes (
    code   text PRIMARY KEY,
    lab_id text NOT NULL REFERENCES infinity.labs (id)
);

CREATE TABLE IF NOT EXISTS infinity.bookings (
    id            text PRIMARY KEY,
    user_id       text NOT NULL REFERENCES infinity.users (id),
    instrument_id text NOT NULL REFERENCES infinity.instruments (id),
    starts_at     timestamptz NOT NULL,
    ends_at       timestamptz NOT NULL,
    status        text NOT NULL CHECK (status IN ('requested', 'confirmed', 'cancelled', 'completed')),
    account_code  text
);

CREATE TABLE IF NOT EXISTS infinity.usage_records (
    id            text PRIMARY KEY,
    instrument_id text NOT NULL REFERENCES infinity.instruments (id),
    user_id       text NOT NULL REFERENCES infinity.users (id),
    booking_id    text REFERENCES infinity.bookings (id),
    starts_at     timestamptz NOT NULL,
    ends_at       timestamptz NOT NULL,
    source        text NOT NULL CHECK (source IN ('scheduled', 'tracked'))
);

CREATE TABLE IF NOT EXISTS infinity.request_templates (
    id          text PRIMARY KEY,
    facility_id text NOT NULL REFERENCES infinity.facilities (id),
    name        text NOT NULL,
    fields      jsonb NOT NULL          -- [{name, label, type, required, options?}]
);

CREATE TABLE IF NOT EXISTS infinity.service_requests (
    id          text PRIMARY KEY,
    user_id     text NOT NULL REFERENCES infinity.users (id),
    template_id text NOT NULL REFERENCES infinity.request_templates (id),
    fields      jsonb NOT NULL DEFAULT '{}'::jsonb,
    status      text NOT NULL CHECK (status IN ('submitted', 'in_progress', 'completed', 'rejected')),
    history     jsonb NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS infinity.samples (
    id         text PRIMARY KEY,
    request_id text NOT NULL REFERENCES infinity.service_requests (id),
    barcode    text UNIQUE NOT NULL,
    state      text NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS infinity.projects (
    id       text PRIMARY KEY,
    name     text NOT NULL,
    currency text NOT NULL DEFAULT 'USD'
);

CREATE TABLE IF NOT EXISTS infinity.project_members (
    project_id text NOT NULL REFERENCES infinity.projects (id),
    user_id    text NOT NULL REFERENCES infinity.users (id),
    role       text NOT NULL,
    PRIMARY KEY (project_id, user_id)
);

CREATE TABLE IF NOT EXISTS infinity.invoices (
    id           text PRIMARY KEY,
    account_code text NOT NULL REFERENCES infinity.account_codes (code),
    period       text NOT NULL,          -- 'YYYY-MM'
    total        numeric(12, 2) NOT NULL,
    UNIQUE (account_code, period)
);

CREATE TABLE IF NOT EXISTS infinity.invoice_lines (
    id            text PRIMARY KEY,
    invoice_id    text NOT NULL REFERENCES infinity.invoices (id),
    description   text NOT NULL,
    instrument_id text REFERENCES infinity.instruments (id),
    qty           numeric(10, 2) NOT NULL,
    unit_price    numeric(10, 2) NOT NULL,
    amount        numeric(12, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS infinity.maintenance_events (
    id             text PRIMARY KEY,
    instrument_id  text NOT NULL REFERENCES infinity.instruments (id),
    kind           text NOT NULL CHECK (kind IN ('preventive', 'repair', 'alert')),
    notes          text,
    occurred_at    timestamptz NOT NULL,
    downtime_hours numeric(6, 2) NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_bookings_user       ON infinity.bookings (user_id);
CREATE INDEX IF NOT EXISTS ix_bookings_instrument ON infinity.bookings (instrument_id, starts_at);
CREATE INDEX IF NOT EXISTS ix_usage_user          ON infinity.usage_records (user_id);
CREATE INDEX IF NOT EXISTS ix_usage_instrument    ON infinity.usage_records (instrument_id, starts_at);
CREATE INDEX IF NOT EXISTS ix_samples_request     ON infinity.samples (request_id);
CREATE INDEX IF NOT EXISTS ix_invoice_lines_inv   ON infinity.invoice_lines (invoice_id);
CREATE INDEX IF NOT EXISTS ix_maint_instrument    ON infinity.maintenance_events (instrument_id, occurred_at);

-- ---------------------------------------------------------------------------
-- echomind — knowledge, actions, audit, evals
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS echomind.knowledge_docs (
    id            text PRIMARY KEY,
    title         text NOT NULL,
    version       text NOT NULL DEFAULT '1',
    visibility    text NOT NULL CHECK (visibility IN ('public', 'lab', 'private')),
    owner_user_id text,
    lab_id        text,
    facility_id   text,
    source_path   text,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS echomind.chunks (
    id            bigserial PRIMARY KEY,
    doc_id        text NOT NULL REFERENCES echomind.knowledge_docs (id) ON DELETE CASCADE,
    ord           int NOT NULL,
    text          text NOT NULL,
    breadcrumb    text,
    embedding     vector(1024),
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    -- denormalized from knowledge_docs so the permission filter is a single-table predicate
    visibility    text NOT NULL CHECK (visibility IN ('public', 'lab', 'private')),
    owner_user_id text,
    lab_id        text,
    facility_id   text,
    UNIQUE (doc_id, ord)
);

CREATE INDEX IF NOT EXISTS ix_chunks_tsv    ON echomind.chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS ix_chunks_vec    ON echomind.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_chunks_filter ON echomind.chunks (visibility, owner_user_id, lab_id);

CREATE TABLE IF NOT EXISTS echomind.actions (
    id          text PRIMARY KEY,
    user_id     text NOT NULL,
    tool        text NOT NULL,
    payload     jsonb NOT NULL,
    status      text NOT NULL CHECK (status IN ('pending', 'approved', 'declined', 'executed', 'failed')),
    approver_id text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    decided_at  timestamptz,
    executed_at timestamptz,
    result      jsonb
);

CREATE INDEX IF NOT EXISTS ix_actions_user   ON echomind.actions (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_actions_status ON echomind.actions (status);

-- Append-only audit trail. `actions` holds current state; golden rule 4 requires that
-- every event (proposed / approved / declined / executed / failed) be recorded, and M5
-- verifies that a single action shows *both* its proposal and its approval.
CREATE TABLE IF NOT EXISTS echomind.audit_log (
    id         bigserial PRIMARY KEY,
    action_id  text REFERENCES echomind.actions (id) ON DELETE CASCADE,
    event      text NOT NULL,
    actor_id   text NOT NULL,
    tool       text,
    detail     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_audit_action ON echomind.audit_log (action_id, created_at);
CREATE INDEX IF NOT EXISTS ix_audit_time   ON echomind.audit_log (created_at DESC);

CREATE TABLE IF NOT EXISTS echomind.eval_runs (
    id      bigserial PRIMARY KEY,
    ran_at  timestamptz NOT NULL DEFAULT now(),
    metrics jsonb NOT NULL
);
