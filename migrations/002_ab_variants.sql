CREATE TABLE experiments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE variants (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    weight FLOAT NOT NULL DEFAULT 1.0
);
CREATE TABLE variant_stats (
    variant_id TEXT NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
    impressions INT NOT NULL DEFAULT 0,
    conversions INT NOT NULL DEFAULT 0,
    PRIMARY KEY (variant_id)
);
