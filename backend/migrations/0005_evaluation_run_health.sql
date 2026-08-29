-- 0005_evaluation_run_health.sql
--
-- Second axis for an evaluation's record: how much of the committee survived.
--
-- WHY A COLUMN AND NOT A NEW `status` VALUE.
--
-- `evaluations.status` answers exactly one question — did this run produce the
-- 24-section report it exists to produce. Three of the sixteen persisted runs
-- answer "yes" while being materially damaged, and all three must STAY
-- `completed`, because the report is real and every `status = 'completed'`
-- reader is right to include it: api/reports.py lists it, knowledge/history.py
-- offers it as a usable prior, the consistency sweep extracts claims from it.
-- Folding degradation into `status` would hide a real report from all three.
--
--   Plasma d5571fd9      six of eight data agents died on a prompt-template
--                        bug. `_calc_score` divides by the weight that scored,
--                        so 0.45 of the table was renormalised to 1.0 and the
--                        number is indistinguishable from a whole-committee
--                        one. -> run_health.score_weight_covered
--   Chainlink 75cf1b3d   the Risk Officer exhausted its tool rounds. `vetoed`
--                        is `risk.output.get("veto", False)`, so an agent that
--                        never answered read as an agent that cleared the
--                        project. -> run_health.risk_officer_ran
--   GMX 8e4b3c83         the Chair errored. -> run_health.chair_decided
--
-- WHY JSONB AND NOT COLUMNS. The shape is an instrument, not an interface: no
-- reader depends on it, nothing joins on it, and the useful fields will change
-- as more failure modes are measured. Columns would mean a migration per
-- measurement. Queries stay cheap either way:
--
--   SELECT p.name, e.status, e.run_health->>'score_weight_covered'
--     FROM evaluations e JOIN projects p ON p.id = e.project_id
--    WHERE (e.run_health->>'score_weight_covered')::numeric < 1.0;
--
-- IDEMPOTENT, per backend/migrations/README.md: `ADD COLUMN IF NOT EXISTS` is
-- a no-op on a volume that already has the column, which is what keeps the
-- fresh-volume path (init.sql) and the live volume from diverging.

ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS run_health JSONB;

-- Partial index, not a full one. The rows worth finding are the small minority
-- that failed; `status = 'completed'` is 16 of 17 and an index over it would be
-- read past on every scan. idx_evaluations_status (init.sql) still covers
-- equality lookups on the common value.
CREATE INDEX IF NOT EXISTS idx_evaluations_status_not_completed
    ON evaluations (status, created_at DESC)
    WHERE status <> 'completed';

COMMENT ON COLUMN evaluations.run_health IS
    'Instrument only: which agents failed, what fraction of the score weight '
    'carried a score, whether the Risk Officer and Chair answered. Never feeds '
    'a prompt, a score or a decision. Written by app/agents/orchestrator.py '
    'build_run_health.';
