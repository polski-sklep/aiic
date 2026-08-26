-- 0004_consistency_findings.sql
--
-- The cross-report consistency audit's ledger.
--
-- Why a table at all, rather than knowledge_chunks or a memory/*.md file:
-- see the long note on `render_active_warnings` in
-- backend/app/knowledge/consistency.py. Short version — findings are versioned
-- records that get superseded, they join back to the evaluations they came
-- from, and a monthly sweep has to be able to ask "have I seen this before?"
-- deterministically. Vector similarity and a markdown file can do neither.
--
-- APPEND-ONLY, and that is load-bearing rather than stylistic.
--
-- CONTRACTS §2.5: past reports are the audit record, and for the 18 June cohort
-- they are the only surviving copy of the reasoning. "Correct them if needed"
-- therefore cannot mean editing a report, and by the same argument it should
-- not mean editing a finding either: an audit trail that can be rewritten
-- cannot evidence what the committee believed and when. So a finding is not a
-- row whose status changes. It is a chain of immutable revisions sharing a
-- `fingerprint`:
--
--     revision 1  observed + first check      status 'open' / 'unverified'
--     revision 2  second check, 24h later     status 'transient' | 'confirmed_error'
--     revision 3  correction                  supersedes_id -> revision 2's id
--
-- Current state is the highest revision per fingerprint. There is no UPDATE and
-- no DELETE anywhere in app/knowledge/consistency.py, against this table or any
-- other. The module reads `reports`, `agent_outputs` and `evaluations` and
-- never writes to them.
--
-- Idempotency ("safe to run twice"):
--   `fingerprint` is a sha256 over (entity, metric, period, and the set of
--   source claims, each keyed by evaluation + section + literal value). Detection
--   is a pure function of the corpus, so an unchanged corpus regenerates the same
--   fingerprint. The UNIQUE (fingerprint, revision) constraint plus
--   ON CONFLICT DO NOTHING in the insert makes a repeated sweep a no-op at the
--   database level, not merely by convention in the caller.
--
-- Idempotent DDL (CREATE ... IF NOT EXISTS) as backend/migrations/README.md
-- requires, so this is a no-op on a fresh volume that already took both tables
-- from init.sql, and creates them on the live volume where init.sql never ran.
-- Both tables are new, so there is no rewrite, no lock on existing data, and no
-- risk to the rows already in the volume.

CREATE TABLE IF NOT EXISTS consistency_findings (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Stable identity of the contradiction across audit runs.
    fingerprint        TEXT NOT NULL,
    -- 1 = observed, 2 = re-checked, 3+ = corrected. Never renumbered.
    revision           INTEGER NOT NULL DEFAULT 1,
    -- Set on a correction: points at the revision this one replaces. The
    -- replaced row is left exactly as written.
    supersedes_id      UUID REFERENCES consistency_findings(id) ON DELETE SET NULL,
    audit_run_id       UUID,

    -- The entity the claims are ABOUT, which is not the project the report was
    -- written for. Keying on the report's own project is precisely what made
    -- the GMX report's claims about Hyperliquid invisible.
    entity             TEXT NOT NULL,
    metric             TEXT NOT NULL,
    as_of_period       TEXT NOT NULL,

    severity           TEXT NOT NULL DEFAULT 'medium',
    -- open | unverified | transient | confirmed_error | resolved | superseded
    status             TEXT NOT NULL DEFAULT 'open',
    spread_pct         NUMERIC(10,2),
    -- True for the "same figure, two different dates" shape, which a value
    -- comparison cannot see and which turns into an unfireable decision rule.
    date_attribution   BOOLEAN NOT NULL DEFAULT FALSE,

    -- The extracted tuples, each carrying its source evaluation, section and
    -- the sentence it came from, so a finding can always be traced back to the
    -- prose without re-running extraction.
    claims             JSONB NOT NULL DEFAULT '[]',
    -- Append-only list of ground-truth observations: [{at, source, ok, value,
    -- detail}]. Two entries are what distinguishes a transient from a
    -- persistent error.
    verifications      JSONB NOT NULL DEFAULT '[]',

    rationale          TEXT,
    -- Pre-rendered so the prompt path is a string read, not a formatting pass.
    warning_text       TEXT,

    first_observed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_checked_at    TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT consistency_findings_fingerprint_revision_key
        UNIQUE (fingerprint, revision)
);

-- "Current state of every finding" is DISTINCT ON (fingerprint) ORDER BY
-- fingerprint, revision DESC — this index is what makes that a cheap scan.
CREATE INDEX IF NOT EXISTS idx_consistency_findings_current
    ON consistency_findings (fingerprint, revision DESC);

-- Rendering the warning block filters on status and orders by severity.
CREATE INDEX IF NOT EXISTS idx_consistency_findings_status
    ON consistency_findings (status, severity);

-- "What do we know about this entity" — the query an agent-facing lookup makes.
CREATE INDEX IF NOT EXISTS idx_consistency_findings_entity
    ON consistency_findings (entity, metric);

-- Bookkeeping for the trigger. `corpus_size` is what "every 10 reports" counts
-- against: the sweep is due when the corpus has grown by 10 since the last
-- completed run, or when that run is 30 days old.
CREATE TABLE IF NOT EXISTS consistency_audit_runs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at      TIMESTAMPTZ,
    status            TEXT NOT NULL DEFAULT 'running',
    corpus_size       INTEGER NOT NULL DEFAULT 0,
    claims_extracted  INTEGER NOT NULL DEFAULT 0,
    conflicts_found   INTEGER NOT NULL DEFAULT 0,
    findings_new      INTEGER NOT NULL DEFAULT 0,
    error             TEXT
);

CREATE INDEX IF NOT EXISTS idx_consistency_audit_runs_started
    ON consistency_audit_runs (started_at DESC);
