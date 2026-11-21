from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import false, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..adapters.yandex_forms import YANDEX_SOURCES, normalize_source
from ..models import DEMO_INTERN_STATUS, Answer, IngestEvent, Intern, Mentor
from ..schemas import CanonicalAnswer

DEMO_INTERN_PREFIX = "demo-feedback-"


class DemoInternError(ValueError):
    pass



def get_or_create_mentor(
    db: Session,
    external_id: str | None,
    name: str | None = None,
) -> Mentor | None:
    if not external_id:
        return None
    mentor = db.scalar(select(Mentor).where(Mentor.external_id == external_id))
    if mentor is None:
        mentor = Mentor(external_id=external_id, name=name or external_id)
        db.add(mentor)
        db.flush()
    elif name and mentor.name != name:
        mentor.name = name
    return mentor


def get_or_create_intern(
    db: Session,
    external_id: str,
    *,
    name: str | None = None,
    direction: str | None = None,
    mentor_external_id: str | None = None,
    start_period: date | None = None,
    status: str | None = None,
) -> tuple[Intern, bool]:
    intern = db.scalar(select(Intern).where(Intern.external_id == external_id))
    demo = status == DEMO_INTERN_STATUS
    if external_id.startswith(DEMO_INTERN_PREFIX) != demo or (
        intern is not None and (intern.status == DEMO_INTERN_STATUS) != demo
    ):
        raise DemoInternError("ID демо-стажёра доступен только для демо-анкет.")
    created = False
    if intern is None:
        intern = Intern(
            external_id=external_id,
            name=name or external_id,
            direction=direction,
            start_period=start_period,
            status=status or ("needs_mapping" if external_id.startswith("unknown") else "active"),
        )
        db.add(intern)
        db.flush()
        created = True
    else:
        if name and (intern.name == intern.external_id or not intern.name):
            intern.name = name
        if direction and intern.direction != direction:
            intern.direction = direction
        if start_period and intern.start_period != start_period:
            intern.start_period = start_period
        if status and intern.status != status:
            intern.status = status

    if mentor_external_id:
        mentor = get_or_create_mentor(db, mentor_external_id)
        if mentor is not None:
            intern.mentor_id = mentor.id
    return intern, created


def _lock_intern_ingest(db: Session, external_id: str) -> None:
    """Serialize answer intake for one participant across transports."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        key = func.hashtextextended(f"mgts-answer-intern:{external_id}", 0)
        db.execute(select(func.pg_advisory_xact_lock(key)))
    elif dialect == "sqlite":
        # Start a write transaction without updating any rows.
        db.execute(update(Intern).where(false()).values(id=Intern.id).execution_options(synchronize_session=False))


def _existing_answer(db: Session, canonical: CanonicalAnswer) -> Answer | None:
    source = normalize_source(canonical.source)
    yandex_source = func.lower(func.replace(Answer.source, "-", "_")).in_(YANDEX_SOURCES)
    same_source = yandex_source if source == "yandex_forms" else Answer.source == source
    existing = db.scalar(
        select(Answer)
        .where(same_source, Answer.external_answer_id == canonical.external_answer_id)
        .order_by(Answer.id)
        .limit(1)
    )
    if existing is not None or source not in {"csv", "yandex_forms"}:
        return existing

    # CSV fallback and the webhook can carry the same provider answer ID.
    other_source = yandex_source if source == "csv" else Answer.source == "csv"
    query = (
        select(Answer)
        .join(Intern)
        .where(
            other_source,
            Answer.external_answer_id == canonical.external_answer_id,
            Answer.period == canonical.period,
            Answer.respondent_type == canonical.respondent_type,
            Intern.external_id == canonical.intern_external_id,
        )
        .order_by(Answer.id)
        .limit(1)
    )
    if canonical.form_id:
        query = query.where(or_(Answer.form_id.is_(None), Answer.form_id == canonical.form_id))
    return db.scalar(query)


def upsert_answer(
    db: Session,
    canonical: CanonicalAnswer,
    *,
    raw_payload: dict[str, Any] | None = None,
    intern_status: str | None = None,
) -> tuple[Answer, bool]:
    """Insert once, retaining the first accepted copy across Yandex and CSV."""
    demo = intern_status == DEMO_INTERN_STATUS
    if canonical.intern_external_id.startswith(DEMO_INTERN_PREFIX) != demo:
        raise DemoInternError("ID демо-стажёра доступен только для демо-анкет.")
    _lock_intern_ingest(db, canonical.intern_external_id)
    existing = _existing_answer(db, canonical)
    if existing is not None:
        if (existing.intern.status == DEMO_INTERN_STATUS) != demo:
            raise DemoInternError("ID демо-стажёра доступен только для демо-анкет.")
        return existing, False

    try:
        with db.begin_nested():
            intern, _ = get_or_create_intern(
                db,
                canonical.intern_external_id,
                status=intern_status,
            )
            mentor = get_or_create_mentor(db, canonical.mentor_external_id)
            if mentor is not None:
                intern.mentor_id = mentor.id

            scores = canonical.scores.model_dump(exclude_none=True)
            answer = Answer(
                source=normalize_source(canonical.source),
                external_answer_id=canonical.external_answer_id,
                form_id=canonical.form_id,
                period=canonical.period,
                respondent_type=canonical.respondent_type,
                respondent_external_id=canonical.respondent_external_id,
                intern_id=intern.id,
                mentor_id=mentor.id if mentor is not None else intern.mentor_id,
                overall_score=float(scores["overall"]),
                scores=scores,
                comment=canonical.comment,
                submitted_at=canonical.submitted_at,
                raw=raw_payload if raw_payload is not None else canonical.raw,
                status="received",
                analysis_status="pending",
            )
            db.add(answer)
            db.flush()
    except IntegrityError:
        # Resolve a same-source ID race without rolling back prior CSV rows.
        existing = _existing_answer(db, canonical)
        if existing is None:
            raise
        if (existing.intern.status == DEMO_INTERN_STATUS) != demo:
            raise DemoInternError("ID демо-стажёра доступен только для демо-анкет.")
        return existing, False
    return answer, True


def log_ingest_event(
    db: Session,
    *,
    source: str,
    payload: dict[str, Any] | None,
    status: str,
    error: str | None = None,
    external_id: str | None = None,
) -> IngestEvent:
    event = IngestEvent(
        source=source,
        external_id=external_id,
        payload=payload,
        status=status,
        error=error,
    )
    db.add(event)
    db.commit()
    return event
