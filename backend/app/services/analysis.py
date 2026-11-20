from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import AIAnalysis, Answer, Intern, Mentor
from ..prompts import PROMPT_VERSION
from ..utils import month_index
from .anonymize import redact_pii
from .llm import BaseProvider, get_provider, provider_model_name

logger = logging.getLogger(__name__)


def all_people_names(db: Session) -> list[str]:
    names = db.scalars(select(Intern.name)).all()
    names += db.scalars(select(Mentor.name)).all()
    return [name for name in names if name]


def _upsert_analysis(db: Session, answer: Answer, **values) -> AIAnalysis:
    analysis = answer.analysis
    if analysis is None:
        analysis = AIAnalysis(answer_id=answer.id, model="unknown", prompt_version=PROMPT_VERSION)
        db.add(analysis)
        answer.analysis = analysis
    for key, value in values.items():
        setattr(analysis, key, value)
    db.flush()
    return analysis


def analyze_answer(
    db: Session,
    answer: Answer,
    *,
    provider: BaseProvider | None = None,
    raise_errors: bool = False,
) -> AIAnalysis:
    """Analyze one answer and persist the result separately from the raw text."""
    if answer.analysis is not None and answer.analysis.status == "done" and not raise_errors:
        return answer.analysis

    names = all_people_names(db)
    redacted_comment = redact_pii(answer.comment or "", names)

    if not redacted_comment:
        answer.analysis_status = "done"
        answer.analysis_error = None
        analysis = _upsert_analysis(
            db,
            answer,
            model="none",
            prompt_version="n/a",
            sentiment="neutral",
            topics=[],
            problem_topics=[],
            summary="Комментарий отсутствует.",
            evidence=[],
            confidence=0.3,
            status="done",
            raw_response={"comment": ""},
            error=None,
        )
        db.commit()
        return analysis

    month = None
    if answer.intern is not None and answer.intern.start_period:
        month = month_index(answer.intern.start_period, answer.period) + 1
    try:
        provider = provider or get_provider()
        provider_result = provider.analyze(
            redacted_comment,
            answer.respondent_type,
            month_index=month,
        )
        result = provider_result.result
        analysis = _upsert_analysis(
            db,
            answer,
            model=provider_model_name(provider),
            prompt_version=provider_result.prompt_version,
            sentiment=result.sentiment,
            topics=result.topics,
            problem_topics=result.problem_topics,
            summary=result.summary,
            evidence=result.evidence,
            confidence=result.confidence,
            status="done",
            raw_response=provider_result.raw,
            error=None,
        )
        answer.analysis_status = "done"
        answer.analysis_error = None
        db.commit()
        return analysis
    except Exception as exc:  # noqa: BLE001 - worker must not die on one answer
        logger.exception("Analysis failed for answer %s", answer.id)
        message = str(exc)[:2000]
        analysis = _upsert_analysis(
            db,
            answer,
            model=provider_model_name(provider) if provider else settings.llm_provider,
            prompt_version=PROMPT_VERSION,
            sentiment=None,
            topics=[],
            problem_topics=[],
            summary=None,
            evidence=[],
            confidence=None,
            status="failed",
            raw_response=None,
            error=message,
        )
        answer.analysis_status = "failed"
        answer.analysis_error = message
        db.commit()
        if raise_errors:
            raise
        return analysis


def _claim_answer(db: Session, answer_id: int) -> Answer | None:
    result = db.execute(
        update(Answer)
        .where(Answer.id == answer_id, Answer.analysis_status == "pending")
        .values(analysis_status="processing")
    )
    db.commit()
    if result.rowcount == 0:
        return None
    return db.get(Answer, answer_id)


def analyze_answer_by_id(
    answer_id: int,
    *,
    provider: BaseProvider | None = None,
    force: bool = False,
) -> AIAnalysis | None:
    db = SessionLocal()
    try:
        if force:
            answer = db.get(Answer, answer_id)
            if answer is None:
                return None
            db.execute(delete(AIAnalysis).where(AIAnalysis.answer_id == answer.id))
            answer.analysis_status = "pending"
            answer.analysis_error = None
            db.commit()
        answer = _claim_answer(db, answer_id)
        if answer is None:
            return None
        return analyze_answer(db, answer, provider=provider)
    finally:
        db.close()


def analyze_pending(
    db: Session,
    *,
    limit: int = 100,
    provider: BaseProvider | None = None,
) -> int:
    ids = db.scalars(
        select(Answer.id)
        .where(Answer.analysis_status == "pending")
        .order_by(Answer.id)
        .limit(limit)
    ).all()
    for answer_id in ids:
        analyze_answer_by_id(answer_id, provider=provider)
    if ids:
        from .rules import recompute_flags

        recompute_flags(db)
    return len(ids)


def reset_analysis_for_intern(db: Session, intern_id: int) -> int:
    answers = db.scalars(select(Answer).where(Answer.intern_id == intern_id)).all()
    for answer in answers:
        db.execute(delete(AIAnalysis).where(AIAnalysis.answer_id == answer.id))
        answer.analysis_status = "pending"
        answer.analysis_error = None
    db.commit()
    return len(answers)


def latest_period(db: Session) -> date | None:
    return db.scalar(select(Answer.period).order_by(Answer.period.desc()).limit(1))
