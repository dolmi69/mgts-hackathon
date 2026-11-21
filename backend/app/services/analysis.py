from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import DEMO_INTERN_STATUS, AIAnalysis, AnalysisJob, Answer, Intern, Mentor, utcnow
from ..prompts import PROMPT_VERSION
from ..schemas import AIResult
from ..utils import month_index
from .anonymize import redact_pii
from .llm import BaseProvider, LLMError, ProviderResult, get_provider, provider_model_name
from .rules import recompute_flags, replace_demo_flags

logger = logging.getLogger(__name__)

MAX_JOB_ATTEMPTS = 3
ANALYSIS_FAILED = "Не удалось разобрать комментарий. Можно повторить анализ."
ANALYSIS_INTERRUPTED = "Анализ был прерван несколько раз. Можно повторить вручную."


def all_people_names(db: Session) -> list[str]:
    names = db.scalars(select(Intern.name)).all()
    names += db.scalars(select(Mentor.name)).all()
    return [name for name in names if name]


def _lock_answer(db: Session, answer_id: int) -> Answer | None:
    # Lock the answer first, so both PostgreSQL and SQLite serialize changes
    # to the lease and the answer even when the job row does not exist yet.
    result = db.execute(
        update(Answer)
        .where(Answer.id == answer_id)
        .values(analysis_status=Answer.analysis_status)
        .execution_options(synchronize_session=False)
    )
    return db.get(Answer, answer_id, populate_existing=True) if result.rowcount else None


def _is_due(deadline: datetime | None, now: datetime) -> bool:
    if deadline is None:
        return True
    # SQLite does not round-trip timezone information.
    return deadline.replace(tzinfo=deadline.tzinfo or timezone.utc) <= now


def _lease_duration() -> timedelta:
    # Each provider attempt can perform OAuth, completion, and one repair call.
    per_attempt = max(0, settings.llm_timeout_seconds) * 4 + 15
    return timedelta(seconds=max(120, (max(0, settings.llm_max_retries) + 1) * per_attempt))


def _store_result(
    db: Session,
    answer: Answer,
    job: AnalysisJob,
    result: ProviderResult | None,
    error: str | None,
    model: str,
) -> AIAnalysis:
    record = answer.analysis
    if record is None:
        record = AIAnalysis(answer_id=answer.id, model=model, prompt_version=PROMPT_VERSION)
        db.add(record)
        answer.analysis = record

    data = result.result if result is not None else None
    record.model = result.model if result is not None else model
    record.prompt_version = result.prompt_version if result is not None else PROMPT_VERSION
    record.sentiment = data.sentiment if data is not None else None
    record.topics = data.topics if data is not None else []
    record.problem_topics = data.problem_topics if data is not None else []
    record.summary = data.summary if data is not None else None
    record.evidence = data.evidence if data is not None else []
    record.confidence = data.confidence if data is not None else None
    record.raw_response = result.raw if result is not None else None
    record.status = "done" if result is not None else "failed"
    record.error = error
    answer.analysis_status = record.status
    answer.analysis_error = error
    job.lease_token = None
    job.lease_expires_at = None
    job.retry_at = None
    db.flush()

    if answer.intern.status == DEMO_INTERN_STATUS:
        replace_demo_flags(db, answer)
    db.commit()
    return record


def _claim_answer(db: Session, answer_id: int) -> str | None:
    answer = _lock_answer(db, answer_id)
    if answer is None or answer.analysis_status not in {"pending", "processing"}:
        db.rollback()
        return None
    now = utcnow()
    job = db.get(AnalysisJob, answer.id, populate_existing=True)
    if job is None:
        job = AnalysisJob(answer_id=answer.id, attempts=0)
        db.add(job)
    deadline = job.lease_expires_at if answer.analysis_status == "processing" else job.retry_at
    if not _is_due(deadline, now):
        db.rollback()
        return None
    if job.attempts >= MAX_JOB_ATTEMPTS:
        _store_result(db, answer, job, None, ANALYSIS_INTERRUPTED, settings.llm_provider)
        return None

    token = str(uuid4())
    job.lease_token = token
    job.lease_expires_at = now + _lease_duration()
    job.retry_at = None
    job.attempts += 1
    answer.analysis_status = "processing"
    answer.analysis_error = None
    db.commit()
    return token


def _request_result(
    db: Session, answer_id: int, provider: BaseProvider | None
) -> ProviderResult | None:
    answer = db.get(Answer, answer_id)
    if answer is None:
        return None
    comment = redact_pii(answer.comment or "", all_people_names(db))
    role = answer.respondent_type
    start = answer.intern.start_period
    month = month_index(start, answer.period) + 1 if start else None
    # Release the read transaction while waiting for the external provider.
    db.rollback()
    if not comment:
        return ProviderResult(
            result=AIResult(summary="Комментарий отсутствует.", confidence=0.3),
            raw={"comment": ""},
            model="none",
            prompt_version="n/a",
        )
    return (provider or get_provider()).analyze(comment, role, month_index=month)


def _finish_claim(
    db: Session,
    answer_id: int,
    token: str,
    result: ProviderResult | None,
    *,
    retryable: bool = False,
    model: str,
) -> AIAnalysis | None:
    answer = _lock_answer(db, answer_id)
    job = db.get(AnalysisJob, answer_id, populate_existing=True)
    # An expired worker may finish after another worker or operator reclaimed
    # the job. Publish only if this attempt still owns the current lease.
    if (
        answer is None
        or answer.analysis_status != "processing"
        or job is None
        or job.lease_token != token
    ):
        db.rollback()
        return None
    if result is None and retryable and job.attempts < MAX_JOB_ATTEMPTS:
        job.lease_token = None
        job.lease_expires_at = None
        job.retry_at = utcnow() + timedelta(seconds=15 * 2 ** (job.attempts - 1))
        answer.analysis_status = "pending"
        db.commit()
        return None

    return _store_result(db, answer, job, result, ANALYSIS_FAILED if result is None else None, model)


def analyze_answer_by_id(
    answer_id: int,
    *,
    provider: BaseProvider | None = None,
    force: bool = False,
) -> AIAnalysis | None:
    with SessionLocal() as db:
        if force and not _reset_answers(db, [answer_id]):
            return None
        token = _claim_answer(db, answer_id)
        if token is None:
            return None

        result = None
        retryable = False
        try:
            result = _request_result(db, answer_id, provider)
        except Exception as exc:  # Keep provider details and user text out of diagnostics.
            db.rollback()
            retryable = isinstance(exc, LLMError) and exc.retryable
            logger.warning("Analysis attempt failed for answer %s (%s)", answer_id, type(exc).__name__)
        model = provider_model_name(provider) if provider else settings.llm_provider
        return _finish_claim(db, answer_id, token, result, retryable=retryable, model=model)


def stale_processing(now: datetime):
    return and_(
        Answer.analysis_status == "processing",
        or_(AnalysisJob.lease_expires_at.is_(None), AnalysisJob.lease_expires_at <= now),
    )


def analyze_pending(
    db: Session,
    *,
    limit: int = 100,
    provider: BaseProvider | None = None,
    intern_id: int | None = None,
) -> int:
    now = utcnow()
    query = (
        select(Answer.id)
        .outerjoin(AnalysisJob)
        .where(
            or_(
                and_(
                    Answer.analysis_status == "pending",
                    or_(AnalysisJob.retry_at.is_(None), AnalysisJob.retry_at <= now),
                ),
                stale_processing(now),
            )
        )
        .order_by(Answer.id)
        .limit(limit)
    )
    if intern_id is not None:
        query = query.where(Answer.intern_id == intern_id)
    ids = db.scalars(query).all()
    db.rollback()
    for answer_id in ids:
        analyze_answer_by_id(answer_id, provider=provider)
    if ids:
        recompute_flags(db)
    return len(ids)


def _reset_answers(db: Session, answer_ids: list[int]) -> int:
    now = utcnow()
    queued = 0
    for answer_id in answer_ids:
        answer = _lock_answer(db, answer_id)
        if answer is None:
            continue
        job = db.get(AnalysisJob, answer_id, populate_existing=True)
        if answer.analysis_status == "processing" and job and not _is_due(job.lease_expires_at, now):
            continue
        if job:
            db.delete(job)
        answer.analysis_status = "pending"
        answer.analysis_error = None
        queued += 1
    db.commit()
    return queued


def reset_analysis_for_intern(db: Session, intern_id: int, answer_id: int | None = None) -> int:
    query = select(Answer.id).where(Answer.intern_id == intern_id).order_by(Answer.id)
    if answer_id is not None:
        query = query.where(Answer.id == answer_id)
    return _reset_answers(db, list(db.scalars(query)))


def latest_period(db: Session) -> date | None:
    return db.scalar(select(Answer.period).order_by(Answer.period.desc()).limit(1))
