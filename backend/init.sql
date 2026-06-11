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
