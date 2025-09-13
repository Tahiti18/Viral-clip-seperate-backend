-- Make the experiments table correct without dropping anything.

-- Ensure table exists (if it already exists, this is a no-op)
CREATE TABLE IF NOT EXISTS experiments (
    id          TEXT PRIMARY KEY,
    job_id      TEXT,
    variant_name TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ensure required columns exist (no error if already there)
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS id            TEXT;
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS job_id        TEXT;
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS variant_name  TEXT;
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS created_at    TIMESTAMPTZ;

-- Ensure NOT NULL where needed (only if column is currently null-able AND has no nulls)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'experiments' AND column_name = 'variant_name'
          AND is_nullable = 'YES'
  ) THEN
    -- If you *know* there is no null data, enforce NOT NULL:
    EXECUTE 'ALTER TABLE experiments ALTER COLUMN variant_name SET NOT NULL';
  END IF;
END
$$;

-- Ensure PRIMARY KEY on id
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM   pg_constraint
    WHERE  conrelid = 'experiments'::regclass
    AND    contype = 'p'
  ) THEN
    ALTER TABLE experiments
      ADD CONSTRAINT experiments_pkey PRIMARY KEY (id);
  END IF;
END
$$;

-- Ensure FK to jobs(id) with ON DELETE CASCADE
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM   pg_constraint
    WHERE  conrelid = 'experiments'::regclass
    AND    contype = 'f'
    AND    conname = 'experiments_job_id_fkey'
  ) THEN
    ALTER TABLE experiments
      ADD CONSTRAINT experiments_job_id_fkey
      FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE;
  END IF;
END
$$;
