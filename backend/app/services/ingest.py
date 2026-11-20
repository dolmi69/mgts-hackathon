from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Answer, IngestEvent, Intern, Mentor
from ..schemas import CanonicalAnswer


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
    status: str = "active",
) -> tuple[Intern, bool]:
    intern = db.scalar(select(Intern).where(Intern.external_id == external_id))
    created = False
    if intern is None:
        intern = Intern(
            external_id=external_id,
            name=name or external_id,
            direction=direction,
            start_period=start_period,
            status=status,
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


def upsert_answer(
    db: Session,
    canonical: CanonicalAnswer,
    *,
    raw_payload: dict[str, Any] | None = None,
) -> tuple[Answer, bool]:
    """Insert an answer once. Duplicate external ids are a no-op."""
    existing = db.scalar(
        select(Answer).where(
            Answer.source == canonical.source,
            Answer.external_answer_id == canonical.external_answer_id,
        )
    )
    if existing is not None:
        return existing, False

    intern, _ = get_or_create_intern(
        db,
        canonical.intern_external_id,
        status="needs_mapping" if canonical.intern_external_id.startswith("unknown") else "active",
    )
    mentor = get_or_create_mentor(db, canonical.mentor_external_id)
    if mentor is not None:
        intern.mentor_id = mentor.id

    scores = canonical.scores.model_dump(exclude_none=True)
    answer = Answer(
        source=canonical.source,
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
    try:
        db.flush()
    except IntegrityError:
        # Concurrent duplicate: keep the first row and return it.
        db.rollback()
        existing = db.scalar(
            select(Answer).where(
                Answer.source == canonical.source,
                Answer.external_answer_id == canonical.external_answer_id,
            )
        )
        if existing is not None:
            return existing, False
        raise
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
