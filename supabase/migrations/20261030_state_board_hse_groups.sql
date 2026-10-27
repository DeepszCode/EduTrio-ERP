-- Additive OCR schema support for State Board HSE Biology, Computer Science,
-- and Arts groups. Existing certificate data and tables remain intact.

ALTER TABLE public.state_board_12th
  ADD COLUMN IF NOT EXISTS group_category text,
  ADD COLUMN IF NOT EXISTS computer_applications_theory_marks numeric,
  ADD COLUMN IF NOT EXISTS computer_applications_practical_marks numeric,
  ADD COLUMN IF NOT EXISTS computer_applications_internal_marks numeric,
  ADD COLUMN IF NOT EXISTS computer_applications_marks numeric;

NOTIFY pgrst, 'reload schema';
