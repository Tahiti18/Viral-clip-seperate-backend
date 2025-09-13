BEGIN;

CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    -- link to jobs, but only if jobs exists
    job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
    variant_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS variants (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS variant_stats (
    id BIGSERIAL PRIMARY KEY,
    variant_id TEXT NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
    impressions INT DEFAULT 0,
    clicks INT DEFAULT 0,
    conversions INT DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_experiments_job_id    ON experiments(job_id);
CREATE INDEX IF NOT EXISTS idx_variants_experiment   ON variants(experiment_id);
CREATE INDEX IF NOT EXISTS idx_variant_stats_variant ON variant_stats(variant_id);

COMMIT;
