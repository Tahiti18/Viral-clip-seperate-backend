DO $$
BEGIN
  CREATE TYPE platform AS ENUM ('value1', 'value2', 'value3');
EXCEPTION
  WHEN duplicate_object THEN
    -- type already exists, skip
    NULL;
END
$$;
