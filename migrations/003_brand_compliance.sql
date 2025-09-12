DO $$ BEGIN
    CREATE TYPE platform AS ENUM ('generic','tiktok','shorts','reels','x','youtube','instagram');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS compliance_packs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT NOT NULL, platform platform NOT NULL DEFAULT 'generic', rules JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO compliance_packs (name, platform, rules) VALUES
('finance-basic','generic', '{"phraseBans":["guaranteed","risk-free","no risk","sure thing"],"claims":{"forbidden":["guaranteed","risk-free","100% returns","get rich quick"]},"disclosures":[{"keywords":["investment","trading","crypto","stocks"],"text":"Not financial advice. Past performance is not indicative of future results."}]}')
ON CONFLICT DO NOTHING;

INSERT INTO compliance_packs (name, platform, rules) VALUES
('health-basic','generic', '{"phraseBans":["cure-all","miracle cure"],"claims":{"forbidden":["cures","heals instantly","permanent cure"]},"disclosures":[{"keywords":["supplement","health","diet"],"text":"Consult a qualified professional. Individual results may vary."}]}')
ON CONFLICT DO NOTHING;
