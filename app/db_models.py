"""SQLAlchemy ORM models for the four T-11 tables. Columns exactly per
the task spec.

Indexes: three only, each matching a real query already in app/main.py's
handlers (actions by agent_id+created_at, approvals by action_id, audit
by created_at for chain walking).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ActionORM(Base):
    __tablename__ = "actions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    resource: Mapped[str] = mapped_column(String, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    params_hash: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Not in T-11's original column list - gap found during the pre-T-14
    # persistence review (no state machine state was ever persisted).
    state: Mapped[str] = mapped_column(String, nullable=False)
    # Feature B (novelty add-on): additive, nullable - a JSONB float
    # array from OpenAI text-embedding-3-small. Null when embedding was
    # never attempted or failed (fail-soft, embedding_degraded=true).
    embedding: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (Index("ix_actions_agent_id_created_at", "agent_id", "created_at"),)


class RiskAssessmentORM(Base):
    __tablename__ = "risk_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[str] = mapped_column(String, ForeignKey("actions.id"), nullable=False)
    reversibility_score: Mapped[float] = mapped_column(Float, nullable=False)
    data_scope_score: Mapped[float] = mapped_column(Float, nullable=False)
    regulatory_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    # L-G: confidence_score actually holds 1 - llm_confidence - the name
    # lies (DB comment carries the full explanation, see migration
    # cbd4d076c66a). uncertainty_score is the same value, honestly named.
    # llm_confidence_raw is the true, uninverted model self-report.
    # Additive, nullable - confidence_score is unchanged and still written.
    uncertainty_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_confidence_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    weights_version: Mapped[str] = mapped_column(String, nullable=False)
    composite: Mapped[float] = mapped_column(Float, nullable=False)
    floor_fired: Mapped[str | None] = mapped_column(String, nullable=True)
    tier: Mapped[str] = mapped_column(String, nullable=False)
    llm_model: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rendered_explanation: Mapped[str] = mapped_column(String, nullable=False)


class ApprovalORM(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[str] = mapped_column(String, ForeignKey("actions.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_params_hash: Mapped[str] = mapped_column(String, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_approvals_action_id", "action_id"),)


class AuditRecordORM(Base):
    """APPEND-ONLY. Application code must never UPDATE or DELETE rows here."""

    __tablename__ = "audit_records"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    prev_hash: Mapped[str] = mapped_column(String, nullable=False)
    entry_hash: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_audit_records_created_at", "created_at"),)
