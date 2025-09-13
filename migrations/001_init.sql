BEGIN;

-- ── Core tables ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orgs (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    id                TEXT PRIMARY KEY,
    lane              SMALLINT NOT NULL,
    max_input_minutes INT      NOT NULL,
    target_multiplier FLOAT    NOT NULL,
    credit_multiplier FLOAT    NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    source_url       TEXT NOT NULL,
    input_minutes    INT  NOT NULL,
    plan_id          TEXT NOT NULL REFERENCES plans(id),
    lane             SMALLINT NOT NULL,
    priority_score   INT DEFAULT 0,
    state            TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    eta_seconds      INT,
    idempotency_key  TEXT
);

CREATE TABLE IF NOT EXISTS job_events (
    id     BIGSERIAL PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    state  TEXT NOT NULL,
    detail JSONB,
    at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS job_sla_audit (
    job_id         TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    target_seconds INT  NOT NULL,
    actual_seconds INT  NOT NULL,
    breached       BOOLEAN NOT NULL,
    remedy         JSONB
);

-- ── Helpful indexes (safe to re-run) ──────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_jobs_org_id        ON jobs(org_id);
CREATE INDEX IF NOT EXISTS idx_jobs_plan_id       ON jobs(plan_id);
CREATE INDEX IF NOT EXISTS idx_jobs_state         ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at    ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_job_events_job_id  ON job_events(job_id);

COMMIT;
