DO $$ BEGIN
    CREATE TYPE experiment_state AS ENUM ('DRAFT','RUNNING','PROMOTED','STOPPED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    CREATE TYPE variant_state AS ENUM ('READY','PAUSED','KILLED','PROMOTED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS experiments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  org_id UUID NOT NULL, name TEXT NOT NULL, platform TEXT NOT NULL, target_metric TEXT NOT NULL,
  min_impressions INTEGER NOT NULL DEFAULT 500, min_runtime_seconds INTEGER NOT NULL DEFAULT 3600,
  prior_alpha INTEGER NOT NULL DEFAULT 1, prior_beta INTEGER NOT NULL DEFAULT 1,
  state experiment_state NOT NULL DEFAULT 'DRAFT', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS variants (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), experiment_id UUID NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
  index INTEGER NOT NULL, state experiment_state NOT NULL DEFAULT 'DRAFT', hook_text TEXT NOT NULL, caption_text TEXT NOT NULL,
  style_preset TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_variant_exp_idx UNIQUE (experiment_id, index)
);
CREATE TABLE IF NOT EXISTS variant_stats (
  variant_id UUID PRIMARY KEY REFERENCES variants(id) ON DELETE CASCADE,
  impressions INTEGER NOT NULL DEFAULT 0, clicks INTEGER NOT NULL DEFAULT 0,
  watch3s INTEGER NOT NULL DEFAULT 0, watch30s INTEGER NOT NULL DEFAULT 0,
  alpha INTEGER NOT NULL DEFAULT 1, beta INTEGER NOT NULL DEFAULT 1,
  last_ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
