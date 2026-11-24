from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

DEMO_INTERN_STATUS = "demo"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Mentor(Base):
    __tablename__ = "mentors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    interns: Mapped[list["Intern"]] = relationship(back_populates="mentor")


class Intern(Base):
    __tablename__ = "interns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    direction: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mentor_id: Mapped[int | None] = mapped_column(ForeignKey("mentors.id"), nullable=True)
    start_period: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    mentor: Mapped[Mentor | None] = relationship(back_populates="interns")
    answers: Mapped[list["Answer"]] = relationship(back_populates="intern")
    flags: Mapped[list["RiskFlag"]] = relationship(back_populates="intern")


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint("source", "external_answer_id", name="uq_answers_source_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), default="generic", index=True)
    external_answer_id: Mapped[str] = mapped_column(String(128), index=True)
    form_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    period: Mapped[date] = mapped_column(Date, index=True)
    respondent_type: Mapped[str] = mapped_column(String(16), index=True)
    respondent_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intern_id: Mapped[int] = mapped_column(ForeignKey("interns.id"), index=True)
    mentor_id: Mapped[int | None] = mapped_column(ForeignKey("mentors.id"), nullable=True)
    overall_score: Mapped[float] = mapped_column(Float)
    scores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    comment: Mapped[str] = mapped_column(Text, default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="received")
    analysis_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    intern: Mapped[Intern] = relationship(back_populates="answers")
    mentor: Mapped[Mentor | None] = relationship()
    analysis: Mapped["AIAnalysis | None"] = relationship(
        back_populates="answer",
        uselist=False,
        cascade="all, delete-orphan",
    )


class DemoResponseSlot(Base):
    __tablename__ = "demo_response_slots"

    intern_id: Mapped[int] = mapped_column(ForeignKey("interns.id", ondelete="CASCADE"), primary_key=True)
    period: Mapped[date] = mapped_column(Date, primary_key=True)
    respondent_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id", ondelete="CASCADE"), unique=True)


class AIAnalysis(Base):
    __tablename__ = "ai_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    answer_id: Mapped[int] = mapped_column(
        ForeignKey("answers.id", ondelete="CASCADE"), unique=True, index=True
    )
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32))
    sentiment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    problem_topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    answer: Mapped[Answer] = relationship(back_populates="analysis")


class RiskFlag(Base):
    __tablename__ = "risk_flags"
    __table_args__ = (
        UniqueConstraint("intern_id", "period", "rule_code", name="uq_flags_intern_period_rule"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    intern_id: Mapped[int] = mapped_column(ForeignKey("interns.id"), index=True)
    period: Mapped[date] = mapped_column(Date, index=True)
    rule_code: Mapped[str] = mapped_column(String(16), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(255))
    explanation: Mapped[str] = mapped_column(Text)
    evidence_answer_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    intern: Mapped[Intern] = relationship(back_populates="flags")
    actions: Mapped[list["SignalAction"]] = relationship(
        back_populates="flag", order_by="SignalAction.id", cascade="all, delete-orphan"
    )


class SignalAction(Base):
    __tablename__ = "signal_actions"
    __table_args__ = (
        UniqueConstraint("flag_id", "state", name="uq_signal_action_flag_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flag_id: Mapped[int] = mapped_column(ForeignKey("risk_flags.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(255))
    assigned_to: Mapped[str] = mapped_column(String(80))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    flag: Mapped[RiskFlag] = relationship(back_populates="actions")


class IngestEvent(Base):
    __tablename__ = "ingest_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="received", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthFailure(Base):
    """Failed dashboard logins, shared by all API processes and kept across restarts."""

    __tablename__ = "auth_failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AnalysisJob(Base):
    """Durable retry and lease state for an existing answer."""

    __tablename__ = "analysis_jobs"

    answer_id: Mapped[int] = mapped_column(
        ForeignKey("answers.id", ondelete="CASCADE"), primary_key=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
