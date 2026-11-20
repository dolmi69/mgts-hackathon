from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_dashboard_auth
from ..config import settings
from ..database import get_db
from ..models import Intern, RiskFlag
from ..schemas import ReanalyzeResult
from ..services.analysis import analyze_pending, reset_analysis_for_intern
from ..services.rules import recompute_flags

router = APIRouter(tags=["interns"], dependencies=[Depends(require_dashboard_auth)])


def _run_batch() -> None:
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        analyze_pending(db, limit=10_000)
    finally:
        db.close()


@router.post("/api/interns/{intern_id}/reanalyze", response_model=ReanalyzeResult)
def reanalyze_intern(
    intern_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ReanalyzeResult:
    intern = db.get(Intern, intern_id)
    if intern is None:
        raise HTTPException(status_code=404, detail="Стажёр не найден")

    queued = reset_analysis_for_intern(db, intern_id)
    if settings.analyze_in_background:
        background_tasks.add_task(_run_batch)
        flags = db.scalar(
            select(RiskFlag.id).where(RiskFlag.intern_id == intern_id).limit(1)
        )
        return ReanalyzeResult(queued=queued, flags=1 if flags else 0)

    analyze_pending(db, limit=10_000)
    flags = recompute_flags(db)
    return ReanalyzeResult(queued=queued, flags=flags)
