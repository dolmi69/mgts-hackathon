from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import UUID4, BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import require_dashboard_auth
from ..config import settings
from ..database import get_db
from ..models import DEMO_INTERN_STATUS, Answer, Intern, RiskFlag
from ..schemas import CanonicalAnswer, Scores
from ..services.analysis import analyze_answer_by_id
from ..services.ingest import upsert_answer

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
    score: int = Field(strict=True, ge=1, le=5)
    comment: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class FeedbackOut(BaseModel):
    submission_id: UUID4
    intern_id: int
    answer_id: int
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
        intern_id=answer.intern_id,
        answer_id=answer.id,
        analysis_status=answer.analysis_status,
        model=answer.analysis.model if answer.analysis else None,
        signal_codes=list(
            db.scalars(
                select(RiskFlag.rule_code)
                .where(RiskFlag.intern_id == answer.intern_id, RiskFlag.period == answer.period)
                .order_by(RiskFlag.rule_code)
            )
        ),
    )


@router.post("", response_model=FeedbackOut, status_code=status.HTTP_202_ACCEPTED)
def submit_feedback(
    submission: FeedbackIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> FeedbackOut:
    now = datetime.now(timezone.utc)
    canonical = CanonicalAnswer(
        source=DEMO_SOURCE,
        external_answer_id=str(submission.submission_id),
        intern_external_id=f"demo-feedback-{submission.submission_id.hex}",
        period=now.date().replace(day=1),
        respondent_type="intern",
        scores=Scores(overall=submission.score),
        comment=submission.comment,
        submitted_at=now,
    )
    try:
        answer, created = upsert_answer(db, canonical)
        if created:
            answer.intern.status = DEMO_INTERN_STATUS
            answer.intern.name = f"Демо-стажёр №{answer.intern_id}"
            db.commit()
    except IntegrityError:
        # Two requests may attempt to create the same synthetic intern.
        db.rollback()
        answer = _find_answer(db, submission.submission_id)
        if answer is None:
            raise

    if (
        answer.intern.status != DEMO_INTERN_STATUS
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
