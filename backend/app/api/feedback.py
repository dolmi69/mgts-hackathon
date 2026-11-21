from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import UUID4, BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import require_dashboard_auth, require_dashboard_write
from ..config import settings
from ..database import get_db
from ..models import DEMO_INTERN_STATUS, Answer, DemoResponseSlot, Intern, RiskFlag
from ..schemas import CanonicalAnswer, RespondentType, Scores
from ..services.analysis import analyze_answer_by_id
from ..services.ingest import DEMO_INTERN_PREFIX, upsert_answer
from ..utils import parse_period

DEMO_SOURCE = "demo_feedback"


def require_demo_feedback() -> None:
    if not settings.demo_feedback_enabled:
        raise HTTPException(status_code=404, detail="Демо-анкета отключена.")


router = APIRouter(
    prefix="/api/demo/feedback",
    tags=["demo feedback"],
    dependencies=[Depends(require_demo_feedback), Depends(require_dashboard_auth)],
)


class FeedbackIn(BaseModel):
    submission_id: UUID4
    scenario_id: UUID4 | None = None
    respondent_type: RespondentType = "intern"
    period: date | None = None
    score: int = Field(strict=True, ge=1, le=5)
    comment: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

    @field_validator("period", mode="before")
    @classmethod
    def parse_month(cls, value: object) -> date | None:
        return None if value is None else parse_period(value)


class FeedbackOut(BaseModel):
    submission_id: UUID4
    scenario_id: UUID4
    intern_id: int
    answer_id: int
    period: date
    respondent_type: RespondentType
    analysis_status: Literal["pending", "processing", "done", "failed"]
    model: str | None = None
    signal_codes: list[str] = Field(default_factory=list)


def _find_answer(db: Session, submission_id: UUID4) -> Answer | None:
    return db.scalar(
        select(Answer)
        .join(Intern)
        .where(
            Answer.source == DEMO_SOURCE,
            Answer.external_answer_id == str(submission_id),
            Intern.status == DEMO_INTERN_STATUS,
        )
    )


def _feedback_out(db: Session, answer: Answer, submission_id: UUID4) -> FeedbackOut:
    return FeedbackOut(
        submission_id=submission_id,
        scenario_id=answer.intern.external_id.removeprefix(DEMO_INTERN_PREFIX),
        intern_id=answer.intern_id,
        answer_id=answer.id,
        period=answer.period,
        respondent_type=answer.respondent_type,
        analysis_status=answer.analysis_status,
        model=answer.analysis.model if answer.analysis else None,
        signal_codes=list(
            db.scalars(
                select(RiskFlag.rule_code)
                .where(
                    RiskFlag.intern_id == answer.intern_id,
                    RiskFlag.period == answer.period,
                    RiskFlag.status == "open",
                )
                .order_by(RiskFlag.rule_code)
            )
        ),
    )


def _find_slot(db: Session, intern_external_id: str, answer: CanonicalAnswer) -> int | None:
    return db.scalar(
        select(Answer.id)
        .join(Intern)
        .where(
            Intern.external_id == intern_external_id,
            Answer.period == answer.period,
            Answer.respondent_type == answer.respondent_type,
        )
        .limit(1)
    )


def _slot_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail="Анкета для этой роли и месяца уже есть. Выбери другой месяц или начни новый сценарий.",
    )


@router.post(
    "",
    response_model=FeedbackOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_dashboard_write)],
)
def submit_feedback(
    submission: FeedbackIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> FeedbackOut:
    now = datetime.now(timezone.utc)
    answer = _find_answer(db, submission.submission_id)
    scenario_id = submission.scenario_id or submission.submission_id
    intern_external_id = f"{DEMO_INTERN_PREFIX}{scenario_id.hex}"
    period = submission.period or (answer.period if answer else now.date().replace(day=1))
    canonical = CanonicalAnswer(
        source=DEMO_SOURCE,
        external_answer_id=str(submission.submission_id),
        intern_external_id=intern_external_id,
        period=period,
        respondent_type=submission.respondent_type,
        scores=Scores(overall=submission.score),
        comment=submission.comment,
        submitted_at=now,
    )
    if answer is None:
        intern = db.scalar(select(Intern).where(Intern.external_id == intern_external_id))
        if (intern is not None and intern.status != DEMO_INTERN_STATUS) or (
            intern is None and submission.scenario_id is not None
        ):
            raise HTTPException(status_code=404, detail="Демо-сценарий не найден.")
        if _find_slot(db, intern_external_id, canonical) is not None:
            raise _slot_conflict()
        try:
            answer, created = upsert_answer(db, canonical, intern_status=DEMO_INTERN_STATUS)
            if created:
                answer.intern.name = f"Демо-стажёр №{answer.intern_id}"
                db.add(DemoResponseSlot(
                    intern_id=answer.intern_id,
                    period=answer.period,
                    respondent_type=answer.respondent_type,
                    answer_id=answer.id,
                ))
                db.commit()
        except IntegrityError:
            db.rollback()
            answer = _find_answer(db, submission.submission_id)
            if answer is None:
                if _find_slot(db, intern_external_id, canonical) is not None:
                    raise _slot_conflict()
                raise

    if (
        answer.intern.status != DEMO_INTERN_STATUS
        or answer.intern.external_id != intern_external_id
        or answer.period != period
        or answer.respondent_type != submission.respondent_type
        or answer.overall_score != submission.score
        or answer.comment != canonical.comment
    ):
        raise HTTPException(status_code=409, detail="Этот запрос уже использован для другой анкеты.")

    if answer.analysis_status == "pending":
        background_tasks.add_task(analyze_answer_by_id, answer.id)
    return _feedback_out(db, answer, submission.submission_id)


@router.get("/{submission_id}", response_model=FeedbackOut)
def feedback_status(submission_id: UUID4, db: Session = Depends(get_db)) -> FeedbackOut:
    answer = _find_answer(db, submission_id)
    if answer is None:
        raise HTTPException(status_code=404, detail="Анкета не найдена.")
    return _feedback_out(db, answer, submission_id)
