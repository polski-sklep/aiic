import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, Integer, Numeric, Boolean, DateTime, ForeignKey
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector
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
