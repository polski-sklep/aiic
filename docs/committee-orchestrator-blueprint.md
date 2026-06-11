# Committee Orchestrator — Project Blueprint

## 1. Overview

A multi-agent AI system that simulates an investment committee for evaluating crypto projects (L1s, L2s, DeFi, infrastructure, middleware, tokens). Generates structured reports with risk assessments, scores, and decision recommendations.

**Stack**: Python (FastAPI + agents) · Next.js 15 (frontend) · Postgres + pgvector (knowledge/state) · Configured Claude or OpenAI provider · Hetzner VPS (colocated with Clawdio/n8n)

**Target**: Full system — orchestrator, API, web UI, knowledge base, transcription pipeline.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    NEXT.JS 15 FRONTEND                      │
│  Google SSO · Report Viewer · Knowledge Base · Version Diff │
│  Review UI · Project Input · Portfolio Dashboard            │
└──────────────────────────┬──────────────────────────────────┘
                           │ REST/WebSocket
┌──────────────────────────▼──────────────────────────────────┐
│                   FASTAPI BACKEND                           │
│  /evaluate · /reports · /memos · /learnings · /portfolio    │
│  /tools/:name · /knowledge · /transcribe                    │
│  Background tasks (Celery/ARQ) · WebSocket for live status  │
└────────┬──────────────────┬─────────────────┬───────────────┘
         │                  │                 │
┌────────▼────────┐ ┌──────▼───────┐ ┌───────▼──────────────┐
│  ORCHESTRATOR   │ │  POSTGRES    │ │  EXTERNAL APIS       │
│  13 Core Agents │ │  + pgvector  │ │  CoinGecko           │
│  + Ray Review   │ │              │ │  DeFiLlama           │
│  Tool Registry  │ │  reports     │ │  Token Terminal       │
│  Message Bus    │ │  learnings   │ │  Dune/Nansen         │
│                 │ │  portfolio   │ │  Snapshot/Tally      │
│                 │ │  embeddings  │ │  Etherscan/Blockscout│
│                 │ │  transcripts │ │  DeepSeek (optional) │
└─────────────────┘ └──────────────┘ └──────────────────────┘
```

---

## 3. Agent Roster (13 Core Agents + Independent Ray Review)

| # | Agent | Model | Role | Veto? |
|---|-------|-------|------|-------|
| 1 | **Committee Chair** | Claude Opus / GPT-4o | Final synthesis, conflict resolution, decision | No |
| 2 | **Tokenomics Analyst** | Claude Sonnet | Supply, distribution, vesting, inflation, utility, value accrual | No |
| 3 | **Governance Analyst** | Claude Sonnet | DAO structure, voting, decentralisation risk, proposal history | No |
| 4 | **On-Chain Analyst** | Claude Sonnet | Tx volume, active addresses, TVL, liquidity depth, smart contract audits | No |
| 5 | **Tech/Infra Analyst** | Claude Sonnet | Architecture, consensus, throughput, security model, upgrade path | No |
| 6 | **Competitive Intel** | Claude Sonnet | Market positioning, SWOT, moat analysis, market share | No |
| 7 | **Field Intel / Sentiment** | Claude Sonnet | Social signals, community health, developer activity, hype vs reality | No |
| 8 | **Risk Officer** | Claude Opus / GPT-4o | Security vulns, regulatory, counterparty, systemic risk | **YES** |
| 9 | **Maturation Scorer** | Claude Opus / GPT-4o | Roadmap execution, team track record, adoption curve, growth potential | No |
| 10 | **Devil's Advocate** | Claude Opus / GPT-4o | Contrarian challenge to all positive assumptions, worst-case scenarios | No |
| 11 | **Portfolio Manager** | Claude Opus / GPT-4o | Portfolio fit, correlation, concentration risk, sizing | No |
| 12 | **Legal / Regulatory** | Claude Sonnet | Token classification, jurisdiction, compliance, entity structure | No |
| 13 | **Report Writer** | Claude Opus / GPT-4o | Compiles all outputs into structured report with scores and recommendation | No |

**Model routing logic:**
- Sonnet: data-gathering and single-domain analysis agents (cost-efficient, fast)
- Opus/GPT-4o: synthesis, judgement, and adversarial agents (higher reasoning)
- Haiku: Knowledge retrieval sub-calls within agents (cheap, fast)
- Provider selection: run with Claude when `ANTHROPIC_API_KEY` is configured, otherwise OpenAI when `OPENAI_API_KEY` is configured

---

## 4. Tool Registry

### 4.1 Market & DeFi Data (19 tools)
| Tool | Source | Data |
|------|--------|------|
| `get_price` | CoinGecko | Current price, market cap, volume |
| `get_price_history` | CoinGecko | Historical OHLCV |
| `get_token_info` | CoinGecko | Supply, circulating, max |
| `get_tvl` | DeFiLlama | Protocol TVL, chain TVL |
| `get_tvl_history` | DeFiLlama | Historical TVL |
| `get_yields` | DeFiLlama | Yield/APY across pools |
| `get_protocol_fees` | DeFiLlama | Revenue, fees |
| `get_protocol_metrics` | Token Terminal | P/S, P/E, revenue |
| `get_fdv_analysis` | CoinGecko + calc | FDV vs MCap ratio, unlock schedule |
| `get_dex_volume` | DeFiLlama | DEX trading volume |
| `get_stablecoin_flows` | DeFiLlama | Stablecoin inflows/outflows |
| `get_bridges_flow` | DeFiLlama | Bridge volume by chain |
| `get_liquidations` | DeFiLlama | Liquidation events |
| `get_market_dominance` | CoinGecko | Sector dominance |
| `get_exchange_flows` | Nansen (if available) | CEX in/outflows |
| `get_funding_rounds` | Crunchbase/manual | VC backing, valuations |
| `get_token_unlocks` | Token Unlocks API | Vesting schedule, cliff dates |
| `get_correlation` | CoinGecko + calc | Price correlation with BTC/ETH |
| `get_volatility` | CoinGecko + calc | 30/90d historical volatility |

### 4.2 On-Chain (7 tools)
| Tool | Source | Data |
|------|--------|------|
| `get_contract_info` | Etherscan/Blockscout | Verified source, deployer |
| `get_tx_stats` | Dune | Tx count, active addresses |
| `get_holder_distribution` | Etherscan/Nansen | Top holders, concentration |
| `get_smart_contract_audits` | Manual/API | Audit reports, findings |
| `get_developer_activity` | GitHub API | Commits, contributors, repos |
| `get_gas_usage` | Etherscan | Gas consumption trends |
| `get_whale_activity` | Nansen/Arkham | Large holder movements |

### 4.3 Governance (13 tools)
| Tool | Source | Data |
|------|--------|------|
| `get_proposals` | Snapshot | Recent proposals, outcomes |
| `get_voting_power` | Snapshot | Distribution of voting power |
| `get_voter_participation` | Snapshot | Turnout rates |
| `get_onchain_governance` | Tally | On-chain proposal history |
| `get_treasury` | Dune/manual | Treasury holdings, runway |
| `get_multisig_info` | Safe API | Signers, threshold |
| `get_delegation_stats` | Tally | Delegation patterns |
| `get_governance_changes` | Snapshot/Tally | Parameter changes history |
| `get_token_voting_power` | Etherscan | Token-weighted voting concentration |
| `get_forum_activity` | Web scrape | Governance forum post frequency |
| `get_dao_structure` | Manual/docs | Legal wrapper, foundation info |
| `get_veto_mechanisms` | Manual/docs | Emergency powers, guardian roles |
| `get_upgrade_authority` | On-chain | Who can upgrade contracts |

### 4.4 Research (12 tools)
| Tool | Source | Data |
|------|--------|------|
| `web_search` | Brave/Serper | General web search |
| `search_twitter` | Twitter API / scrape | Sentiment, announcements |
| `search_discord_telegram` | Manual/API | Community health signals |
| `get_news` | CryptoPanic/RSS | Recent news mentions |
| `get_whitepaper` | URL fetch | Whitepaper/docs content |
| `search_knowledge_base` | Postgres/pgvector | Past evaluations, IC notes |
| `get_team_info` | LinkedIn/web | Team background, track record |
| `get_competitors` | Manual/LLM | Identified competitors |
| `get_ecosystem_map` | Manual/DeFiLlama | Ecosystem projects, integrations |
| `get_regulatory_status` | Web search | Regulatory actions, compliance |
| `get_security_incidents` | Rekt.news/DeFiSafety | Past hacks, exploits |
| `get_partnership_history` | Web/announcements | Key partnerships, integrations |

---

## 5. Database Schema (Postgres + pgvector)

```sql
-- Enable vector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Core tables
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    ticker TEXT,
    chain TEXT,
    category TEXT, -- L1, L2, DeFi, Infrastructure, etc.
    website TEXT,
    coingecko_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id),
    status TEXT DEFAULT 'pending', -- pending, running, completed, failed
    triggered_by TEXT, -- user_id or 'auto'
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE agent_outputs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_id UUID REFERENCES evaluations(id),
    agent_name TEXT NOT NULL,
    model_used TEXT,
    output JSONB NOT NULL, -- structured output per agent
    score NUMERIC(4,2), -- agent-specific score if applicable
    tokens_used INTEGER,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_id UUID REFERENCES evaluations(id),
    version INTEGER DEFAULT 1,
    content JSONB NOT NULL, -- full structured report
    summary TEXT,
    recommendation TEXT, -- INVEST, PASS, WATCH, VETO
    overall_score NUMERIC(4,2),
    risk_score NUMERIC(4,2),
    vetoed BOOLEAN DEFAULT FALSE,
    veto_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE learnings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id),
    evaluation_id UUID REFERENCES evaluations(id),
    content TEXT NOT NULL,
    category TEXT, -- risk_pattern, success_signal, red_flag, etc.
    embedding vector(1536),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE transcripts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT,
    source TEXT, -- zoom, manual, etc.
    raw_text TEXT NOT NULL,
    summary TEXT,
    embedding vector(1536),
    call_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE portfolio (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id),
    status TEXT, -- active, exited, watching
    entry_date DATE,
    entry_price NUMERIC,
    allocation_pct NUMERIC(5,2),
    notes TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE knowledge_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT, -- transcript, learning, report, external
    source_id UUID,
    content TEXT NOT NULL,
    embedding vector(1536),
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_knowledge_embedding ON knowledge_chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_learnings_embedding ON learnings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_transcripts_embedding ON transcripts
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_agent_outputs_eval ON agent_outputs(evaluation_id);
CREATE INDEX idx_reports_eval ON reports(evaluation_id);
```

---

## 6. API Routes (FastAPI)

```
POST   /api/evaluate              # Trigger new evaluation
GET    /api/evaluate/{id}         # Get evaluation status
GET    /api/evaluate/{id}/stream  # WebSocket for live agent updates

GET    /api/reports               # List all reports
GET    /api/reports/{id}          # Get report by ID
GET    /api/reports/{id}/diff     # Diff between report versions

GET    /api/projects              # List evaluated projects
POST   /api/projects              # Add project manually
GET    /api/projects/{id}         # Project details + history

POST   /api/tools/{name}         # Execute a specific tool
GET    /api/tools                 # List available tools

GET    /api/learnings             # Search learnings
POST   /api/learnings             # Add manual learning

GET    /api/portfolio             # Current portfolio
POST   /api/portfolio             # Add/update position

GET    /api/knowledge/search      # Semantic search across knowledge base
POST   /api/knowledge/ingest      # Ingest new content (transcript, doc, etc.)

POST   /api/transcribe            # Upload audio → transcribe → store
GET    /api/transcripts           # List transcripts
GET    /api/transcripts/{id}      # Get transcript

POST   /api/auth/google           # Google SSO
GET    /api/auth/me               # Current user
```

---

## 7. Orchestration Flow

```
Input (project name/URL/token address)
    │
    ▼
[1] Committee Chair receives task
    │
    ├──► [2] Knowledge Agent: search KB for prior evaluations, related transcripts
    │
    ├──► [3] PARALLEL DATA GATHERING (Sonnet agents):
    │    ├── Tokenomics Analyst ──► tools: get_token_info, get_fdv_analysis, get_token_unlocks
    │    ├── On-Chain Analyst ────► tools: get_tx_stats, get_holder_distribution, get_contract_info
    │    ├── Tech/Infra Analyst ─► tools: get_developer_activity, get_whitepaper, web_search
    │    ├── Governance Analyst ─► tools: get_proposals, get_voting_power, get_treasury
    │    ├── Competitive Intel ──► tools: get_competitors, get_market_dominance, get_tvl
    │    ├── Field Intel ────────► tools: search_twitter, get_news, search_discord_telegram
    │    └── Legal/Regulatory ──► tools: get_regulatory_status, get_dao_structure
    │
    ├──► [4] SYNTHESIS PHASE (Opus agents, sequential):
    │    ├── Risk Officer ──────► reviews all agent outputs, CAN VETO
    │    ├── Maturation Scorer ─► scores maturity based on gathered data
    │    ├── Devil's Advocate ──► challenges positive findings
    │    └── Portfolio Manager ─► assesses fit with current portfolio
    │
    ├──► [5] Report Writer: compiles everything into structured report
    │
    └──► [6] Committee Chair: final review, recommendation, publish
              │
              ▼
         Store: report, agent_outputs, learnings → Postgres
         Notify: WebSocket → frontend
```

**Estimated API costs per evaluation:**
- 7 Sonnet agents × ~4K tokens each ≈ 28K tokens (~$0.08)
- 4 Opus agents × ~8K tokens each ≈ 32K tokens (~$0.48-0.96)
- Report Writer × ~12K tokens ≈ 12K tokens (~$0.18-0.36)
- Chair × ~6K tokens ≈ 6K tokens (~$0.09-0.18)
- Knowledge/Haiku calls ≈ 10K tokens (~$0.003)
- **Total estimate: ~$0.85-1.60 per evaluation**

---

## 8. Directory Structure

```
committee-orchestrator/
├── backend/                      # Python (FastAPI)
│   ├── pyproject.toml
│   ├── alembic/                  # DB migrations
│   │   └── versions/
│   ├── app/
│   │   ├── main.py               # FastAPI app entry
│   │   ├── config.py             # Settings, env vars
│   │   ├── database.py           # Postgres connection, pgvector
│   │   ├── models/               # SQLAlchemy models
│   │   │   ├── project.py
│   │   │   ├── evaluation.py
│   │   │   ├── report.py
│   │   │   ├── learning.py
│   │   │   ├── transcript.py
│   │   │   └── portfolio.py
│   │   ├── api/                  # Route handlers
│   │   │   ├── evaluate.py
│   │   │   ├── reports.py
│   │   │   ├── projects.py
│   │   │   ├── tools.py
│   │   │   ├── knowledge.py
│   │   │   ├── transcribe.py
│   │   │   ├── portfolio.py
│   │   │   └── auth.py
│   │   ├── agents/               # Agent definitions
│   │   │   ├── base.py           # BaseAgent class
│   │   │   ├── orchestrator.py   # Main orchestration logic
│   │   │   ├── chair.py
│   │   │   ├── tokenomics.py
│   │   │   ├── governance.py
│   │   │   ├── onchain.py
│   │   │   ├── tech_infra.py
│   │   │   ├── competitive.py
│   │   │   ├── field_intel.py
│   │   │   ├── risk_officer.py
│   │   │   ├── maturation.py
│   │   │   ├── devils_advocate.py
│   │   │   ├── portfolio_mgr.py
│   │   │   ├── legal.py
│   │   │   └── report_writer.py
│   │   ├── tools/                # Tool implementations
│   │   │   ├── registry.py       # Tool registry + dispatch
│   │   │   ├── coingecko.py
│   │   │   ├── defillama.py
│   │   │   ├── etherscan.py
│   │   │   ├── snapshot.py
│   │   │   ├── tally.py
│   │   │   ├── github_api.py
│   │   │   ├── dune.py
│   │   │   ├── web_search.py
│   │   │   ├── twitter.py
│   │   │   └── transcription.py
│   │   ├── llm/                  # LLM abstraction layer
│   │   │   ├── provider.py       # LLMProvider base class
│   │   │   ├── claude.py         # Anthropic implementation
│   │   │   ├── openai.py         # OpenAI provider
│   │   │   └── router.py         # Model routing logic
│   │   ├── knowledge/            # Knowledge/RAG layer
│   │   │   ├── embeddings.py     # Embedding generation
│   │   │   ├── search.py         # Semantic search
│   │   │   └── ingest.py         # Content ingestion pipeline
│   │   └── utils/
│   │       ├── prompts.py        # Prompt templates
│   │       └── scoring.py        # Scoring/normalisation
│   └── tests/
│
├── frontend/                     # Next.js 15
│   ├── package.json
│   ├── next.config.ts
│   ├── tailwind.config.ts
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx              # Dashboard
│   │   ├── evaluate/
│   │   │   └── page.tsx          # New evaluation input
│   │   ├── reports/
│   │   │   ├── page.tsx          # Report list
│   │   │   └── [id]/page.tsx     # Report detail + diff
│   │   ├── knowledge/
│   │   │   └── page.tsx          # Knowledge base search
│   │   ├── portfolio/
│   │   │   └── page.tsx          # Portfolio view
│   │   └── api/auth/
│   │       └── [...nextauth].ts  # Google SSO
│   ├── components/
│   │   ├── ReportViewer.tsx
│   │   ├── AgentStatusPanel.tsx  # Live agent progress
│   │   ├── ScoreCard.tsx
│   │   ├── DiffViewer.tsx
│   │   └── KnowledgeSearch.tsx
│   └── lib/
│       ├── api.ts                # Backend API client
│       └── auth.ts               # Auth utilities
│
├── docker-compose.yml            # Postgres + backend + frontend
├── .env.example
└── README.md
```

---

## 9. Build Milestones

### Milestone 1: Foundation (Week 1)
- [ ] Postgres + pgvector on Hetzner (Docker)
- [ ] FastAPI skeleton with config, DB models, migrations
- [ ] LLM abstraction layer (Claude and OpenAI provider support)
- [ ] BaseAgent class with tool-calling and structured output
- [ ] 3 tools working: `get_price`, `get_tvl`, `web_search`
- [ ] Single agent end-to-end test (Tokenomics Analyst)

### Milestone 2: Agent Fleet (Week 2)
- [ ] All 13 core agents with prompts and tool bindings
- [ ] Orchestrator: parallel data-gathering → sequential synthesis
- [ ] Tool registry: all planned tools stubbed, 15+ implemented
- [ ] Knowledge base: embedding pipeline + semantic search
- [ ] Report generation: structured JSON → Markdown

### Milestone 3: API + Transcription (Week 3)
- [ ] All FastAPI routes operational
- [ ] WebSocket for live evaluation progress
- [ ] Transcription pipeline (Whisper API or local whisper.cpp)
- [ ] Knowledge ingestion: transcripts → chunks → embeddings
- [ ] Learning loop: post-evaluation extraction

### Milestone 4: Frontend + Polish (Week 4)
- [ ] Next.js app with Google SSO
- [ ] Evaluation trigger UI
- [ ] Report viewer with scores, charts, version diff
- [ ] Knowledge base search UI
- [ ] Portfolio dashboard
- [ ] Agent status panel (live WebSocket)

### Milestone 5: Iteration (Ongoing)
- [ ] Prompt tuning based on real evaluations
- [ ] Tool coverage expansion
- [ ] Cost optimisation (caching, batching)
- [ ] n8n integration for automated triggers
- [ ] Telegram notifications via Clawdio

---

## 10. Infrastructure (Hetzner VPS)

```
Existing:
  - Clawdio (OpenClaw) — running 24/7
  - n8n — workflow automation
  - Tailscale — VPN mesh

Add:
  - Postgres 16 + pgvector (Docker container)
  - FastAPI backend (Docker container, port 8100)
  - Next.js frontend (Docker container, port 3100)
  - Redis (for Celery/ARQ task queue, port 6379)
  - Nginx reverse proxy (or Caddy) for HTTPS

Docker Compose manages all new services.
Tailscale for secure access from Mac.
```

**Resource estimate:**
- Postgres: ~256MB RAM idle, scales with data
- FastAPI: ~128MB RAM idle, spikes during evaluations
- Next.js: ~128MB RAM
- Redis: ~64MB RAM
- Total new: ~576MB RAM baseline

---

## 11. Key Dependencies

### Backend (Python)
```
fastapi>=0.115
uvicorn
sqlalchemy[asyncio]>=2.0
asyncpg
alembic
pgvector
anthropic>=0.40
openai>=1.50
httpx
pydantic>=2.0
python-jose[cryptography]  # JWT
celery or arq              # Task queue
whisper or openai           # Transcription
tiktoken                    # Token counting
```

### Frontend (Node.js)
```
next@15
react@19
tailwindcss
next-auth               # Google SSO
swr or react-query      # Data fetching
react-diff-viewer       # Report diffs
recharts                # Score visualisation
lucide-react            # Icons
```

---

## 12. Environment Variables

```bash
# LLM
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/committee

# External APIs
COINGECKO_API_KEY=        # Free tier works, pro for higher limits
ETHERSCAN_API_KEY=
DUNE_API_KEY=
GITHUB_TOKEN=
SNAPSHOT_API_URL=https://hub.snapshot.org/graphql
BRAVE_SEARCH_API_KEY=     # or SERPER_API_KEY

# Auth
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
JWT_SECRET=

# Transcription
WHISPER_MODEL=base        # or use OpenAI API

# App
BACKEND_URL=http://localhost:8100
FRONTEND_URL=http://localhost:3100
```

---

## 13. Risk Officer Veto Logic

The Risk Officer is the only agent with veto power. Veto triggers:

```python
VETO_TRIGGERS = {
    "critical_security": "Unaudited contracts with >$10M TVL, known exploit patterns",
    "regulatory_red_flag": "Active SEC/DOJ investigation, securities classification risk",
    "rug_pull_signals": "Anonymous team + concentrated supply + no timelock",
    "liquidity_trap": "Illiquid markets, wash trading evidence, fake volume >50%",
    "team_fraud": "Known scammer connections, falsified credentials",
}
```

When vetoed, evaluation completes but report is flagged. Chair can override with documented reasoning.

---

## 14. Scoring Framework

Each agent produces domain scores (0-100). Final score is weighted:

| Agent | Weight | Domain |
|-------|--------|--------|
| Tokenomics | 15% | Token design quality |
| On-Chain | 12% | Network health, usage |
| Tech/Infra | 15% | Technical soundness |
| Governance | 8% | Decentralisation quality |
| Competitive | 10% | Market positioning |
| Field Intel | 5% | Community/sentiment |
| Risk | 15% | Risk-adjusted (inverted) |
| Maturation | 10% | Growth trajectory |
| Legal | 5% | Compliance readiness |
| Portfolio Fit | 5% | Diversification value |

**Recommendation thresholds:**
- ≥75: INVEST
- 60-74: WATCH
- <60: PASS
- Veto: overrides regardless of score

---

## Next Step

This blueprint now serves as the implementation reference for the current build.
