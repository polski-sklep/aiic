-- backfill_report_failed_status.sql
--
-- MANUAL. NOT A MIGRATION. Nothing runs this for you — see the README beside
-- it. It rewrites existing production rows, so it ends in ROLLBACK and you
-- have to change that word yourself.
--
-- WHAT IT CORRECTS
--
-- Five evaluations are recorded `completed` whose Report Writer produced no
-- `sections`, i.e. no report. Re-measured on the live database, 29 Aug 2026
-- (the corpus has grown to 20 rows since; these five are unchanged):
--
--   Polkadot     5e6e4f2d  2026-04-14  Error code: 429 ... exceeded your quota
--   Hyperliquid  b028881a  2026-04-16  Error code: 429 ... exceeded your quota
--   Chainlink    b22be475  2026-06-01  Error code: 429 ... exceeded your quota
--   Aave         0f48a034  2026-06-11  Error code: 429 ... exceeded your quota
--   Hyperliquid  e2d96b62  2026-08-25  keys: summary, raw_output, parse_error
--
-- In all four 429 runs the whole committee died with the Report Writer (14 of
-- 14, 15 of 15 agents errored), not the Report Writer alone. The status is
-- still `report_failed` rather than something new: `status` answers one
-- question — was a report produced — and the answer is the same. HOW MUCH else
-- died is the second statement's job.
--
-- WHY THE CLASSIFICATION LIVES IN ONE PLACE BELOW
--
-- The rule "did this run produce a report" now has two implementations: this
-- file and `orchestrator.report_deliverable_state`. Two copies of one rule is
-- the drift failure PROJECT_DECISIONS D15 exists to record, and the earlier
-- draft of this script had already drifted from the Python in two ways — it
-- accepted `"sections": {}` as a report, and it counted an INSUFFICIENT_DATA
-- chair as having decided. Neither shape occurs in today's corpus (verified:
-- zero rows), so both were latent rather than live, which is exactly how this
-- class of defect stays invisible.
--
-- The fix is `run_classification` below: ONE temporary view, built once,
-- matching the Python predicate for predicate, read by every statement in this
-- file. There is still a second copy of the rule — it cannot be helped, this is
-- SQL and that is Python — but there is no longer a third, and the two are
-- written adjacently enough to diff.
--
-- WHAT IT DOES NOT TOUCH
--
--   * `calibration_records`. One orphaned INSUFFICIENT_DATA row (Aave,
--     2026-06-11, evaluation_id NULL) came from 0f48a034 and is arguably
--     fiction. Deleting rows from the calibration ledger is a different
--     decision with a different blast radius — CONTRACTS §2.6 says do not
--     mutate it without orchestrator approval — so it is reported, not done.
--   * `agent_outputs`, `reports`. The evidence stays exactly as written.
--   * Anything that is not currently `completed`.
--   * Evaluations with no `agent_outputs` at all. ENS c8f3947d is the one such
--     row: it raised out of api/evaluate.py and persisted nothing, so there is
--     nothing to reconstruct a run_health from and it correctly keeps NULL.
--
-- EVERY WRITE IS IDEMPOTENT. Statement 3 is guarded on `status = 'completed'`,
-- statement 4 on `run_health IS NULL`. Running it twice moves nothing —
-- verified 29 Aug 2026 against a production-seeded volume: `UPDATE 0/UPDATE 0`.

\set ON_ERROR_STOP on


-- ===========================================================================
-- 0. The classification, once. Mirrors app/agents/orchestrator.py:
--      report_deliverable_state  -> report_usable, report_failure_reason
--      _agent_failed             -> the failure predicate
--      build_run_health          -> every key written by statement 4
--      DATA_AGENT_NAMES          -> data_agents
--      _DEGRADATION_ONLY         -> degradation_only
--      SCORE_WEIGHTS             -> weights
-- ===========================================================================
CREATE TEMP VIEW weights(agent_name, weight) AS VALUES
    ('tokenomics_analyst', 0.15), ('onchain_analyst',   0.12),
    ('tech_infra_analyst', 0.15), ('governance_analyst',0.08),
    ('competitive_intel',  0.10), ('field_intel',       0.05),
    ('risk_officer',       0.15), ('maturation_scorer', 0.10),
    ('legal_regulatory',   0.05), ('portfolio_manager', 0.05);

CREATE TEMP VIEW data_agents(agent_name) AS VALUES
    ('tokenomics_analyst'), ('governance_analyst'), ('onchain_analyst'),
    ('tech_infra_analyst'), ('competitive_intel'),  ('field_intel'),
    ('legal_regulatory'),   ('technical_analyst');

-- DATA_AGENT_NAMES plus the four synthesis agents whose loss degrades a run
-- without destroying it. report_writer and committee_chair are deliberately
-- absent; risk_officer is absent because it is neither.
CREATE TEMP VIEW degradation_only(agent_name) AS
    SELECT agent_name FROM data_agents
    UNION ALL VALUES ('maturation_scorer'), ('devils_advocate'),
                     ('portfolio_manager'), ('ray_dalio');

-- `_agent_failed`: an AgentResult.error, or base.py's parse_output fallback
-- (a `parse_error` key), or an `error` key in the output.
CREATE TEMP VIEW agent_state AS
    SELECT ao.evaluation_id,
           ao.agent_name,
           ao.score,
           ao.output,
           (ao.error IS NOT NULL
            OR ao.output ? 'error'
            OR ao.output ? 'parse_error')                       AS failed
      FROM agent_outputs ao;

CREATE TEMP VIEW run_classification AS
WITH rw AS (
    SELECT evaluation_id,
           output,
           -- `report_deliverable_state`: sections must be present, an object,
           -- and non-empty. `output ? 'sections'` alone is NOT the rule — the
           -- Python requires `isinstance(sections, dict) and sections`.
           (output ? 'sections'
            AND jsonb_typeof(output -> 'sections') = 'object'
            AND output -> 'sections' <> '{}'::jsonb)            AS usable,
           (error IS NOT NULL OR output ? 'error')              AS call_failed,
           (output ? 'parse_error')                             AS unparseable
      FROM agent_outputs
     WHERE agent_name = 'report_writer'
)
SELECT ags.evaluation_id,
       coalesce(bool_or(rw.usable), false)                      AS report_usable,
       CASE
           WHEN coalesce(bool_or(rw.usable), false)  THEN NULL
           WHEN bool_or(rw.call_failed)              THEN 'call_failed'
           WHEN bool_or(rw.unparseable)              THEN 'unparseable'
           ELSE 'no_sections'
       END                                                      AS report_failure_reason,
       count(*)                                                 AS agents_run,
       count(*) FILTER (WHERE ags.failed)                       AS agents_failed,
       coalesce(array_agg(ags.agent_name ORDER BY ags.agent_name)
                FILTER (WHERE ags.failed), '{}')                AS failed_agents,
       coalesce(array_agg(ags.agent_name ORDER BY ags.agent_name)
                FILTER (WHERE ags.failed AND ags.agent_name IN
                        (SELECT agent_name FROM degradation_only)), '{}')
                                                                AS degraded_only_failures,
       count(*) FILTER (WHERE ags.agent_name IN
                        (SELECT agent_name FROM data_agents))    AS data_agents_total,
       coalesce(array_agg(ags.agent_name ORDER BY ags.agent_name)
                FILTER (WHERE ags.failed AND ags.agent_name IN
                        (SELECT agent_name FROM data_agents)), '{}')
                                                                AS data_agents_failed,
       round(coalesce(sum(w.weight) FILTER (WHERE ags.score IS NOT NULL), 0)::numeric
             / (SELECT sum(weight) FROM weights), 3)            AS score_weight_covered,
       coalesce(bool_or(ags.agent_name = 'risk_officer' AND NOT ags.failed), false)
                                                                AS risk_officer_ran,
       -- `build_run_health`: the Chair's result must be intact AND the decision
       -- must be a decision. CHAIR_FAILED and INSUFFICIENT_DATA are not.
       coalesce(bool_or(ags.agent_name = 'committee_chair'
                        AND NOT ags.failed
                        AND coalesce(ags.output ->> 'decision', '') <> ''
                        AND coalesce(ags.output ->> 'decision', '')
                            NOT IN ('CHAIR_FAILED', 'INSUFFICIENT_DATA')), false)
                                                                AS chair_decided,
       coalesce(bool_or(ags.agent_name = 'risk_officer'
                        AND coalesce((ags.output ->> 'veto')::boolean, false)), false)
                                                                AS vetoed
  FROM agent_state ags
  LEFT JOIN weights w ON w.agent_name = ags.agent_name
  LEFT JOIN rw      ON rw.evaluation_id = ags.evaluation_id
 GROUP BY ags.evaluation_id;


-- ===========================================================================
-- 1. PREVIEW — reads only. Nothing below this changes until statement 3.
-- ===========================================================================
\echo '--- Rows that statement 3 would move out of completed ---'

SELECT p.name                     AS project,
       left(e.id::text, 8)        AS evaluation,
       e.status                   AS current_status,
       e.created_at::date         AS ran_on,
       rc.report_failure_reason   AS would_become_reason,
       left(coalesce(ao.output ->> 'error', ao.error, ao.output ->> 'parse_error'), 60)
                                  AS evidence
  FROM evaluations e
  JOIN projects p            ON p.id = e.project_id
  JOIN run_classification rc ON rc.evaluation_id = e.id
  LEFT JOIN agent_outputs ao ON ao.evaluation_id = e.id AND ao.agent_name = 'report_writer'
 WHERE e.status = 'completed'
   AND NOT rc.report_usable
 ORDER BY e.created_at;

\echo '--- Runs that KEEP completed but were degraded (statement 4 records this) ---'

SELECT p.name                        AS project,
       left(e.id::text, 8)           AS evaluation,
       rc.agents_failed,
       rc.score_weight_covered       AS weight_that_scored,
       rc.risk_officer_ran,
       rc.chair_decided
  FROM evaluations e
  JOIN projects p            ON p.id = e.project_id
  JOIN run_classification rc ON rc.evaluation_id = e.id
 WHERE rc.agents_failed > 0
 ORDER BY e.created_at;


-- ===========================================================================
-- 2. Guard. The column statement 4 writes must exist, which means migration
--    0005 must have been applied. With ON_ERROR_STOP set at the top, this
--    stops the script rather than merely printing.
-- ===========================================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'evaluations' AND column_name = 'run_health'
    ) THEN
        RAISE EXCEPTION
            'evaluations.run_health is missing. Apply migration 0005 first: '
            'docker compose exec backend python -m app.database';
    END IF;
END $$;


BEGIN;

-- ===========================================================================
-- 3. The correction. `completed` -> `report_failed` for runs with no report.
-- ===========================================================================
UPDATE evaluations e
   SET status = 'report_failed',
       error  = coalesce(e.error, '') ||
                CASE WHEN coalesce(e.error, '') = '' THEN '' ELSE ' | ' END ||
                'No report was produced: ' ||
                CASE rc.report_failure_reason
                    WHEN 'call_failed' THEN 'the Report Writer''s model call failed'
                    WHEN 'unparseable' THEN 'the Report Writer''s response could not be parsed'
                    ELSE 'the Report Writer returned an object with no sections'
                END ||
                '. Status corrected by backfill_report_failed_status.sql; the row ' ||
                'was recorded ''completed'' when it was written.'
  FROM run_classification rc
 WHERE rc.evaluation_id = e.id
   AND e.status = 'completed'
   AND NOT rc.report_usable;


-- ===========================================================================
-- 4. `run_health` for every historical run, derived from `agent_outputs`.
--
--    Stamped `"backfilled": true` and `"backfilled_at"`, so a reconstructed
--    record can never be mistaken for one written at run time. Same safeguard
--    the calibration backfill uses (PROJECT_DECISIONS D5).
--
--    `run_health IS NULL` guards it: a row already carrying a record written
--    by the pipeline is left alone.
-- ===========================================================================
UPDATE evaluations e
   SET run_health = jsonb_build_object(
           'report_usable',          rc.report_usable,
           'report_failure_reason',  rc.report_failure_reason,
           'agents_run',             rc.agents_run,
           'agents_failed',          rc.agents_failed,
           'failed_agents',          to_jsonb(rc.failed_agents),
           'degraded_only_failures', to_jsonb(rc.degraded_only_failures),
           'data_agents_total',      rc.data_agents_total,
           'data_agents_failed',     to_jsonb(rc.data_agents_failed),
           'score_weight_covered',   rc.score_weight_covered,
           'risk_officer_ran',       rc.risk_officer_ran,
           'chair_decided',          rc.chair_decided,
           'vetoed',                 rc.vetoed,
           -- Reconstructed from agent_outputs months after the fact. Two keys
           -- the pipeline never writes, so the provenance is unambiguous.
           'backfilled',             true,
           'backfilled_at',          to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SSOF')
       )
  FROM run_classification rc
 WHERE rc.evaluation_id = e.id
   AND e.run_health IS NULL;


-- ===========================================================================
-- 5. Result, inside the transaction, before you decide.
-- ===========================================================================
\echo '--- State after the writes (still uncommitted) ---'

SELECT p.name                                    AS project,
       left(e.id::text, 8)                       AS evaluation,
       e.status,
       e.run_health->>'report_failure_reason'    AS reason,
       e.run_health->>'score_weight_covered'     AS weight,
       e.run_health->>'risk_officer_ran'         AS risk_ok,
       e.run_health->>'chair_decided'            AS chair_ok
  FROM evaluations e
  JOIN projects p ON p.id = e.project_id
 ORDER BY e.created_at;


-- ===========================================================================
-- CHANGE THIS TO `COMMIT;` TO APPLY. It is ROLLBACK on purpose.
-- ===========================================================================
ROLLBACK;
