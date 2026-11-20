from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import dashboard, health, imports, interns, webhooks
from .config import settings
from .database import SessionLocal, init_db
from .seed_data import seed_database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings.seed_on_startup:
        db = SessionLocal()
        try:
            stats = seed_database(db, analyze=settings.seed_analyze)
            logger.info("Startup seed: %s", stats)
        finally:
            db.close()
    yield


app = FastAPI(
    title=settings.app_name,
    description=(
        "Дашборд сигналов по стажёрам: вебхуки анкет, разбор комментариев LLM, "
        "объяснимые правила риска и исходные ответы рядом с выводом."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(webhooks.router)
app.include_router(imports.router)
app.include_router(dashboard.router)
app.include_router(interns.router)

frontend_dir = Path(settings.frontend_dir)
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
else:
    logger.warning("Frontend directory not found: %s", frontend_dir)
