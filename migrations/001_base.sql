-- Base schema + seeds
CREATE TABLE IF NOT EXISTS orgs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    lane SMALLINT NOT NULL,
    max_input_minutes INT NOT NULL,
    target_multiplier FLOAT NOT NULL,
    credit_multiplier FLOAT NOT NULL
);
CREATE TABLE IF NOT EXISTS templates (
    id TEXT PRIMARY KEY,
    org_id TEXT NULL REFERENCES orgs(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    aspect TEXT NOT NULL,
    layers_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    caption_style_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS media (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    source_id TEXT NULL,
    duration_ms INT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS timelines (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    clip_id TEXT NULL,
    template_id TEXT NULL REFERENCES templates(id) ON DELETE SET NULL,
    aspect TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    input_minutes INT NOT NULL,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    lane SMALLINT NOT NULL,
    priority_score INT DEFAULT 0,
    state TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    eta_seconds INT,
    idempotency_key TEXT
);
CREATE TABLE IF NOT EXISTS job_events (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    detail JSONB,
    at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS job_sla_audit (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    target_seconds INT NOT NULL,
    actual_seconds INT NOT NULL,
    breached BOOLEAN NOT NULL,
    remedy JSONB
);
CREATE TABLE IF NOT EXISTS renders (
    id TEXT PRIMARY KEY,
    timeline_id TEXT NOT NULL REFERENCES timelines(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    progress FLOAT NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO orgs (id, name) VALUES ('demo', 'Demo Org') ON CONFLICT (id) DO NOTHING;
INSERT INTO plans (id, lane, max_input_minutes, target_multiplier, credit_multiplier) VALUES
('starter', 1, 30, 1.0, 1.0),
('pro', 2, 90, 1.2, 1.1),
('elite', 3, 180, 1.6, 1.3)
ON CONFLICT (id) DO NOTHING;
INSERT INTO templates (id, org_id, name, aspect) VALUES
('tpl_default', 'demo', 'Bold Captions', '9:16')
ON CONFLICT (id) DO NOTHING;
INSERT INTO projects (id, org_id, title) VALUES
('proj_demo', 'demo', 'Welcome Project')
ON CONFLICT (id) DO NOTHING;
INSERT INTO timelines (id, project_id, template_id, aspect, payload_json) VALUES
('tl_demo', 'proj_demo', 'tpl_default', '9:16', '{}')
ON CONFLICT (id) DO NOTHING;
