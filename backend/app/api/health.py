from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..schemas import HealthOut

router = APIRouter(tags=["system"])


@router.get("/api/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    db.execute(text("SELECT 1"))
    return HealthOut(
        status="ok",
        db="ok",
        llm_provider=settings.llm_provider,
        app=settings.app_name,
    )
