-- 0002_calibration_outcome_notes.sql
--
-- CONTRACTS.md §3.3: add `outcome_notes text` to calibration_records.
--
-- Consumed by agent/calibration. Per CONTRACTS §3.2 a backfilled checkpoint
-- MUST be marked as such in outcome_notes, so update_checkpoint cannot ship
-- without this column.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, so this is a no-op on any volume that
-- already picked the column up from init.sql.

ALTER TABLE calibration_records
    ADD COLUMN IF NOT EXISTS outcome_notes text;
