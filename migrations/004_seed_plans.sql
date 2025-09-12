INSERT INTO plans (id, lane, max_input_minutes, target_multiplier, credit_multiplier)
VALUES
  ('express', 0, 20, 0.5, 1.5),
  ('priority', 1, 60, 1.0, 1.0),
  ('standard', 2, 180, 1.5, 0.8)
ON CONFLICT (id) DO NOTHING;
