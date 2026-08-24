-- 0003_calibration_signposts_review_date.sql
--
-- Give a WATCH a falsifier and an expiry date.
--
-- The Chair already produces both: `signposts` (agents/chair.py — "events that
-- would cause you to revisit this decision") and `review_date` ("when to
-- re-evaluate"). Neither has ever reached the ledger. `signposts` survived only
-- inside the evaluation result blob and `review_date` was dropped entirely, so
-- the committee has been stating its own falsification criteria and the system
-- has been throwing them away.
--
-- That is most of why handoff §6.5 concludes that WATCH is free and
-- unfalsifiable: four of six usable records are WATCH at 53-66, a WATCH commits
-- to nothing, expires never, and cannot be scored wrong. Storing a dated review
-- and a named observable that would flip the call turns it into a testable
-- short-horizon prediction — gradeable at n=6. This is a persistence gap, not a
-- prompting gap; the material is already being generated (docs/adr/0002,
-- "Separable: the conviction question").
--
-- `signposts` is jsonb rather than text[] because it arrives as a JSON array
-- straight off the Chair's output and jsonb round-trips it without a
-- lossy conversion; `review_date` is date rather than timestamptz because the
-- Chair emits a calendar day ("2026-04-11") and a spurious time-of-day would
-- imply precision that is not there.
--
-- Idempotent (ADD COLUMN IF NOT EXISTS), so this is a no-op on a fresh volume
-- that already picked both columns up from init.sql, and additive-only on the
-- populated production volume: no rewrite, no default, no lock beyond the brief
-- ACCESS EXCLUSIVE that a catalog-only ADD COLUMN takes on Postgres 11+.

ALTER TABLE calibration_records
    ADD COLUMN IF NOT EXISTS signposts jsonb;

ALTER TABLE calibration_records
    ADD COLUMN IF NOT EXISTS review_date date;

-- Finding a WATCH whose review date has come is the query this exists to serve.
CREATE INDEX IF NOT EXISTS idx_calibration_review_date
    ON calibration_records (review_date)
    WHERE review_date IS NOT NULL;
