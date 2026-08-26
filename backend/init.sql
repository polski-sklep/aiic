-- backend/init.sql
--
-- Postgres runs this ONLY when initialising an empty data directory. It will
-- never run again against the live volume. It is a fresh-volume fast path;
-- `backend/migrations/` is authoritative and runs on every volume.
--
-- Any schema change here MUST also ship as an idempotent numbered migration in
-- backend/migrations/ in the same commit. See backend/migrations/README.md.

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Projects
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    ticker TEXT,
    chain TEXT,
    category TEXT,
    website TEXT,
    coingecko_id TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Evaluations
CREATE TABLE evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'pending',
    triggered_by TEXT,
    config JSONB DEFAULT '{}',
    error TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_evaluations_project ON evaluations(project_id);
CREATE INDEX idx_evaluations_status ON evaluations(status);

-- Agent outputs
CREATE TABLE agent_outputs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_id UUID REFERENCES evaluations(id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    model_used TEXT,
    output JSONB NOT NULL,
    score NUMERIC(5,2),
    tokens_input INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    latency_ms INTEGER,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_agent_outputs_eval ON agent_outputs(evaluation_id);

-- Reports
CREATE TABLE reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_id UUID REFERENCES evaluations(id) ON DELETE CASCADE,
    version INTEGER DEFAULT 1,
    content JSONB NOT NULL,
    summary TEXT,
    recommendation TEXT,
    overall_score NUMERIC(5,2),
    risk_score NUMERIC(5,2),
    vetoed BOOLEAN DEFAULT FALSE,
    veto_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_reports_eval ON reports(evaluation_id);

-- Learnings
CREATE TABLE learnings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    evaluation_id UUID REFERENCES evaluations(id) ON DELETE SET NULL,
    content TEXT NOT NULL,
    category TEXT,
    embedding vector(1536),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_learnings_embedding ON learnings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Transcripts
CREATE TABLE transcripts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT,
    source TEXT,
    raw_text TEXT NOT NULL,
    summary TEXT,
    embedding vector(1536),
    call_date TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_transcripts_embedding ON transcripts
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Portfolio
CREATE TABLE portfolio (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'watching',
    entry_date DATE,
    entry_price NUMERIC,
    current_price NUMERIC,
    allocation_pct NUMERIC(5,2),
    notes TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Knowledge chunks (RAG)
CREATE TABLE knowledge_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT NOT NULL,
    source_id UUID,
    content TEXT NOT NULL,
    embedding vector(1536),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_knowledge_embedding ON knowledge_chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_knowledge_source ON knowledge_chunks(source_type, source_id);

-- Calibration records
CREATE TABLE calibration_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_id UUID REFERENCES evaluations(id) ON DELETE SET NULL,
    project_name TEXT NOT NULL,
    ticker TEXT,
    coingecko_id TEXT,
    category TEXT,
    recommendation TEXT NOT NULL,
    overall_score NUMERIC(5,2),
    chair_confidence TEXT,
    vetoed BOOLEAN DEFAULT FALSE,
    entry_price_usd NUMERIC,
    entry_market_cap_usd NUMERIC,
    entry_captured_at TIMESTAMPTZ,
    btc_price_at_entry NUMERIC,
    eth_price_at_entry NUMERIC,
    price_30d NUMERIC,
    price_90d NUMERIC,
    price_180d NUMERIC,
    checked_30d_at TIMESTAMPTZ,
    checked_90d_at TIMESTAMPTZ,
    checked_180d_at TIMESTAMPTZ,
    btc_price_30d NUMERIC,
    btc_price_90d NUMERIC,
    btc_price_180d NUMERIC,
    return_30d_pct NUMERIC(10,2),
    return_90d_pct NUMERIC(10,2),
    return_180d_pct NUMERIC(10,2),
    alpha_vs_btc_30d_pct NUMERIC(10,2),
    alpha_vs_btc_90d_pct NUMERIC(10,2),
    alpha_vs_btc_180d_pct NUMERIC(10,2),
    outcome_notes TEXT,
    -- Mirrored from backend/migrations/0003_calibration_signposts_review_date.sql.
    -- The Chair already emits both and the ledger used to discard both, which is
    -- most of why a WATCH is unfalsifiable (handoff 6.5).
    signposts JSONB,
    review_date DATE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_calibration_recommendation ON calibration_records(recommendation);
CREATE INDEX idx_calibration_entry_captured_at ON calibration_records(entry_captured_at);
CREATE INDEX idx_calibration_evaluation_id ON calibration_records(evaluation_id);
CREATE INDEX idx_calibration_review_date ON calibration_records(review_date) WHERE review_date IS NOT NULL;

-- Cross-report consistency audit ledger.
-- Mirrored from backend/migrations/0004_consistency_findings.sql — that file is
-- authoritative and carries the full rationale. This block is the fresh-volume
-- fast path only (CONTRACTS §3.3).
--
-- Append-only: a finding is a chain of immutable revisions sharing a
-- `fingerprint`, current state being the highest revision. Corrections
-- supersede, they never edit — reports are the audit record (CONTRACTS §2.5)
-- and so is this table.
CREATE TABLE consistency_findings (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fingerprint        TEXT NOT NULL,
    revision           INTEGER NOT NULL DEFAULT 1,
    supersedes_id      UUID REFERENCES consistency_findings(id) ON DELETE SET NULL,
    audit_run_id       UUID,
    entity             TEXT NOT NULL,
    metric             TEXT NOT NULL,
    as_of_period       TEXT NOT NULL,
    severity           TEXT NOT NULL DEFAULT 'medium',
    status             TEXT NOT NULL DEFAULT 'open',
    spread_pct         NUMERIC(10,2),
    date_attribution   BOOLEAN NOT NULL DEFAULT FALSE,
    claims             JSONB NOT NULL DEFAULT '[]',
    verifications      JSONB NOT NULL DEFAULT '[]',
    rationale          TEXT,
    warning_text       TEXT,
    first_observed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_checked_at    TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT consistency_findings_fingerprint_revision_key
        UNIQUE (fingerprint, revision)
);

CREATE INDEX idx_consistency_findings_current
    ON consistency_findings (fingerprint, revision DESC);
CREATE INDEX idx_consistency_findings_status
    ON consistency_findings (status, severity);
CREATE INDEX idx_consistency_findings_entity
    ON consistency_findings (entity, metric);

CREATE TABLE consistency_audit_runs (
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

CREATE INDEX idx_consistency_audit_runs_started
    ON consistency_audit_runs (started_at DESC);
