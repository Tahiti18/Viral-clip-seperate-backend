CREATE EXTENSION IF NOT EXISTS pgcrypto;
DO $$ BEGIN
   CREATE TYPE job_state AS ENUM ('CREATED','QUEUED','INGESTING','TRANSCRIBING','ANALYZING','EDITING','RENDERING','UPLOADING','COMPLETED','FAILED','TIMED_OUT','CANCELED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS orgs ( id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT NOT NULL );
CREATE TABLE IF NOT EXISTS plans (
  id TEXT PRIMARY KEY, lane SMALLINT NOT NULL CHECK (lane BETWEEN 0 AND 2),
  max_input_minutes INTEGER NOT NULL, target_multiplier NUMERIC(4,2) NOT NULL, credit_multiplier NUMERIC(4,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), org_id UUID NOT NULL REFERENCES orgs(id),
  source_url TEXT NOT NULL, input_minutes INTEGER NOT NULL CHECK (input_minutes > 0),
  plan_id TEXT NOT NULL REFERENCES plans(id), lane SMALLINT NOT NULL, priority_score INTEGER NOT NULL DEFAULT 0,
  state job_state NOT NULL DEFAULT 'CREATED', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), eta_seconds INTEGER, idempotency_key TEXT,
  UNIQUE (org_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_jobs_lane_state ON jobs(lane, state, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);

CREATE TABLE IF NOT EXISTS job_events (
  id BIGSERIAL PRIMARY KEY, job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  state job_state NOT NULL, detail JSONB, at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS job_sla_audit (
  job_id UUID PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE, target_seconds INTEGER NOT NULL,
  actual_seconds INTEGER NOT NULL, breached BOOLEAN NOT NULL, remedy JSONB
);

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_jobs_updated_at ON jobs;
CREATE TRIGGER trg_jobs_updated_at BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE PROCEDURE touch_updated_at();
