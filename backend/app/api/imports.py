from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..adapters.csv_adapter import parse_answers_csv, parse_interns_csv
from ..auth import require_dashboard_write
from ..config import settings
from ..database import SessionLocal, get_db
from ..models import DEMO_INTERN_STATUS, Answer
from ..schemas import ImportResult
from ..services.analysis import analyze_pending
from ..services.ingest import DemoInternError, get_or_create_intern, upsert_answer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["import"], dependencies=[Depends(require_dashboard_write)])


def run_analysis_batch() -> None:
    db = SessionLocal()
    try:
        analyze_pending(db, limit=10_000)
    except Exception:  # noqa: BLE001
        logger.exception("Batch analysis failed")
    finally:
        db.close()


@router.post("/api/import/csv", response_model=ImportResult)
async def import_csv(
    background_tasks: BackgroundTasks,
    answers_file: UploadFile = File(..., description="CSV с ответами анкет"),
    interns_file: UploadFile | None = File(default=None, description="Справочник стажёров"),
    db: Session = Depends(get_db),
) -> ImportResult:
    result = ImportResult()

    if interns_file is not None:
        intern_rows, intern_errors = parse_interns_csv(await interns_file.read())
        result.errors.extend(intern_errors)
        for row in sorted(intern_rows, key=lambda item: item["external_id"]):
            if row.get("status") == "mentor":
                continue
            try:
                if row.get("status") == DEMO_INTERN_STATUS:
                    raise DemoInternError("Демо-стажёр не импортируется в программу.")
                _intern, created = get_or_create_intern(
                    db,
                    row["external_id"],
                    name=row.get("name"),
                    direction=row.get("direction"),
                    mentor_external_id=row.get("mentor_external_id"),
                    start_period=row.get("start_period"),
                    status=row.get("status") or "active",
                )
            except DemoInternError as exc:
                result.errors.append(str(exc))
                continue
            if created:
                result.interns_created += 1
            else:
                result.interns_updated += 1
        db.commit()

    answer_rows, answer_errors = parse_answers_csv(await answers_file.read())
    result.errors.extend(answer_errors)
    # Acquire transaction locks in the same order in concurrent imports.
    for canonical in sorted(answer_rows, key=lambda item: item.intern_external_id):
        canonical.source = "csv"
        try:
            _answer, created = upsert_answer(db, canonical)
        except DemoInternError as exc:
            result.errors.append(str(exc))
            continue
        if created:
            result.imported += 1
        else:
            result.duplicates += 1
    db.commit()

    pending = db.scalar(
        select(func.count(Answer.id)).where(Answer.analysis_status == "pending")
    )
    result.pending_analysis = int(pending or 0)
    if settings.analyze_in_background and result.pending_analysis:
        background_tasks.add_task(run_analysis_batch)
    return result
