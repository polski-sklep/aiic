import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    ticker = Column(String)
    chain = Column(String)
    category = Column(String)
    website = Column(String)
    coingecko_id = Column(String)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    status = Column(String, default="pending")
    triggered_by = Column(String)
    config = Column(JSONB, default=dict)
    error = Column(Text)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utcnow)


class AgentOutput(Base):
    __tablename__ = "agent_outputs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evaluation_id = Column(UUID(as_uuid=True), ForeignKey("evaluations.id", ondelete="CASCADE"))
    agent_name = Column(String, nullable=False)
    model_used = Column(String)
    output = Column(JSONB, nullable=False)
    score = Column(Numeric(5, 2))
    tokens_input = Column(Integer, default=0)
    tokens_output = Column(Integer, default=0)
    latency_ms = Column(Integer)
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Report(Base):
    __tablename__ = "reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evaluation_id = Column(UUID(as_uuid=True), ForeignKey("evaluations.id", ondelete="CASCADE"))
    version = Column(Integer, default=1)
    content = Column(JSONB, nullable=False)
    summary = Column(Text)
    recommendation = Column(String)
    overall_score = Column(Numeric(5, 2))
    risk_score = Column(Numeric(5, 2))
    vetoed = Column(Boolean, default=False)
    veto_reason = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Learning(Base):
    __tablename__ = "learnings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"))
    evaluation_id = Column(UUID(as_uuid=True), ForeignKey("evaluations.id", ondelete="SET NULL"))
    content = Column(Text, nullable=False)
    category = Column(String)
    embedding = Column(Vector(1536))
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String)
    source = Column(String)
    raw_text = Column(Text, nullable=False)
    summary = Column(Text)
    embedding = Column(Vector(1536))
    call_date = Column(DateTime(timezone=True))
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Portfolio(Base):
    __tablename__ = "portfolio"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    status = Column(String, default="watching")
    entry_date = Column(DateTime)
    entry_price = Column(Numeric)
    current_price = Column(Numeric)
    allocation_pct = Column(Numeric(5, 2))
    notes = Column(Text)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_type = Column(String, nullable=False)
    source_id = Column(UUID(as_uuid=True))
    content = Column(Text, nullable=False)
    embedding = Column(Vector(1536))
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class CalibrationRecord(Base):
    __tablename__ = "calibration_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evaluation_id = Column(UUID(as_uuid=True), ForeignKey("evaluations.id", ondelete="SET NULL"), nullable=True)
    project_name = Column(String, nullable=False)
    ticker = Column(String)
    coingecko_id = Column(String)
    category = Column(String)
    recommendation = Column(String, nullable=False)
    overall_score = Column(Numeric(5, 2))
    chair_confidence = Column(String)
    vetoed = Column(Boolean, default=False)
    entry_price_usd = Column(Numeric)
    entry_market_cap_usd = Column(Numeric)
    entry_captured_at = Column(DateTime(timezone=True))
    btc_price_at_entry = Column(Numeric)
    eth_price_at_entry = Column(Numeric)
    price_30d = Column(Numeric)
    price_90d = Column(Numeric)
    price_180d = Column(Numeric)
    checked_30d_at = Column(DateTime(timezone=True))
    checked_90d_at = Column(DateTime(timezone=True))
    checked_180d_at = Column(DateTime(timezone=True))
    btc_price_30d = Column(Numeric)
    btc_price_90d = Column(Numeric)
    btc_price_180d = Column(Numeric)
    return_30d_pct = Column(Numeric(10, 2))
    return_90d_pct = Column(Numeric(10, 2))
    return_180d_pct = Column(Numeric(10, 2))
    alpha_vs_btc_30d_pct = Column(Numeric(10, 2))
    alpha_vs_btc_90d_pct = Column(Numeric(10, 2))
    alpha_vs_btc_180d_pct = Column(Numeric(10, 2))
    # Added by backend/migrations/0002_calibration_outcome_notes.sql.
    # CONTRACTS §3.2: a backfilled checkpoint must be marked as such here.
    outcome_notes = Column(Text)
    # Added by backend/migrations/0003_calibration_signposts_review_date.sql.
    # The Chair emits both (agents/chair.py `signposts`, `review_date`) and the
    # ledger used to discard both, so a WATCH had no falsifier and no expiry.
    signposts = Column(JSONB)
    review_date = Column(Date)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class ConsistencyFinding(Base):
    """One revision of a cross-report contradiction.

    Added by backend/migrations/0004_consistency_findings.sql. Append-only: a
    finding is a chain of immutable revisions sharing ``fingerprint``, and the
    current state is the highest revision. A re-check appends; a correction
    appends with ``supersedes_id`` set. Nothing updates or deletes — reports are
    the audit record (CONTRACTS §2.5) and so is this ledger.

    ``entity`` is the entity the claims are ABOUT, not the project the report
    was written for. That distinction is the whole point: the GMX report's
    claims about Hyperliquid are invisible to anything keyed on the report's own
    project.
    """

    __tablename__ = "consistency_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fingerprint = Column(Text, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    supersedes_id = Column(
        UUID(as_uuid=True), ForeignKey("consistency_findings.id", ondelete="SET NULL")
    )
    audit_run_id = Column(UUID(as_uuid=True))
    entity = Column(Text, nullable=False)
    metric = Column(Text, nullable=False)
    as_of_period = Column(Text, nullable=False)
    severity = Column(Text, nullable=False, default="medium")
    status = Column(Text, nullable=False, default="open")
    spread_pct = Column(Numeric(10, 2))
    date_attribution = Column(Boolean, nullable=False, default=False)
    claims = Column(JSONB, nullable=False, default=list)
    verifications = Column(JSONB, nullable=False, default=list)
    rationale = Column(Text)
    warning_text = Column(Text)
    first_observed_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_checked_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "fingerprint", "revision", name="consistency_findings_fingerprint_revision_key"
        ),
    )


class ConsistencyAuditRun(Base):
    """Bookkeeping for the "every 10 reports or monthly" trigger.

    ``corpus_size`` is the count the "every 10 reports" rule counts against, so
    the due check is a single query and needs no scheduler state.
    """

    __tablename__ = "consistency_audit_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = Column(DateTime(timezone=True))
    status = Column(Text, nullable=False, default="running")
    corpus_size = Column(Integer, nullable=False, default=0)
    claims_extracted = Column(Integer, nullable=False, default=0)
    conflicts_found = Column(Integer, nullable=False, default=0)
    findings_new = Column(Integer, nullable=False, default=0)
    error = Column(Text)
