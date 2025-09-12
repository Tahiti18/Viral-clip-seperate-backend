CREATE TYPE platform AS ENUM ('youtube','tiktok','instagram','generic');
CREATE TABLE compliance_packs (
    id TEXT PRIMARY KEY,
    platform platform NOT NULL,
    rules JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE brand_docs (
    id TEXT PRIMARY KEY,
    brand_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
