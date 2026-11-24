from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_dashboard_auth, require_dashboard_write
from ..database import SessionLocal, get_db
from ..models import AIAnalysis, AnalysisJob, Answer, Intern, RiskFlag, utcnow
from ..schemas import ReanalyzeResult
from ..services.analysis import (
    analyze_answer_by_id,
    analyze_pending,
    reset_analysis_for_intern,
    stale_processing,
)
from ..services.rules import recompute_flags

router = APIRouter(tags=["interns"], dependencies=[Depends(require_dashboard_auth)])


class AnalysisQueueOut(BaseModel):
    pending: int
    processing: int
    stale_processing: int
    failed: int
    oldest_pending_at: datetime | None
    latest_failed_at: datetime | None


def _run_batch(intern_id: int) -> None:
    with SessionLocal() as db:
        analyze_pending(db, limit=10_000, intern_id=intern_id)


def _run_one(answer_id: int) -> None:
    analyze_answer_by_id(answer_id)
    with SessionLocal() as db:
        recompute_flags(db)


def _open_flags(db: Session, intern_id: int) -> int:
    count = db.scalar(
        select(func.count(RiskFlag.id)).where(
            RiskFlag.intern_id == intern_id, RiskFlag.status == "open"
        )
    )
    return int(count or 0)


@router.post(
    "/api/interns/{intern_id}/reanalyze",
    response_model=ReanalyzeResult,
    dependencies=[Depends(require_dashboard_write)],
)
def reanalyze_intern(
    intern_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ReanalyzeResult:
    if db.get(Intern, intern_id) is None:
        raise HTTPException(status_code=404, detail="Стажёр не найден")

    queued = reset_analysis_for_intern(db, intern_id)
    if queued:
        # Never inside the request: a real model takes seconds per answer. The
        # lease keeps the worker and this task from doing the same answer twice.
        background_tasks.add_task(_run_batch, intern_id)
    return ReanalyzeResult(queued=queued, flags=_open_flags(db, intern_id))


@router.post(
    "/api/interns/{intern_id}/answers/{answer_id}/retry",
    response_model=ReanalyzeResult,
    dependencies=[Depends(require_dashboard_write)],
)
def retry_answer(
    intern_id: int,
    answer_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ReanalyzeResult:
    answer = db.get(Answer, answer_id)
    if answer is None or answer.intern_id != intern_id:
        raise HTTPException(status_code=404, detail="Ответ не найден")

    queued = reset_analysis_for_intern(db, intern_id, answer_id)
    if queued:
        # Never inside the request: a real model takes seconds per answer. The
        # lease keeps the worker and this task from doing the same answer twice.
        background_tasks.add_task(_run_one, answer_id)
    return ReanalyzeResult(queued=queued, flags=_open_flags(db, intern_id))


@router.get("/api/analysis/queue", response_model=AnalysisQueueOut)
def analysis_queue(db: Session = Depends(get_db)) -> AnalysisQueueOut:
    pending = Answer.analysis_status == "pending"
    processing = Answer.analysis_status == "processing"
    failed = Answer.analysis_status == "failed"
    counts = db.execute(
        select(
            func.count().filter(pending).label("pending"),
            func.count().filter(processing).label("processing"),
            func.count().filter(stale_processing(utcnow())).label("stale_processing"),
            func.count().filter(failed).label("failed"),
            func.min(Answer.created_at).filter(pending).label("oldest_pending_at"),
            func.max(AIAnalysis.updated_at).filter(failed).label("latest_failed_at"),
        )
        .select_from(Answer)
        .outerjoin(AnalysisJob)
        .outerjoin(AIAnalysis)
    ).mappings().one()
    return AnalysisQueueOut.model_validate(dict(counts))
