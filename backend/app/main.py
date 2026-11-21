from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .security import BodySizeLimitMiddleware, SecurityHeadersMiddleware, SameOriginMiddleware

from .api import dashboard, feedback, health, imports, interns, signals, webhooks
from .auth import app_env, validate_deployment_auth
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
    validate_deployment_auth()
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
    # The API map is a convenience for local development only.
    docs_url="/docs" if app_env() == "dev" else None,
    redoc_url="/redoc" if app_env() == "dev" else None,
    openapi_url="/openapi.json" if app_env() == "dev" else None,
)

# Order: the last added middleware runs first.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(SameOriginMiddleware)
app.add_middleware(BodySizeLimitMiddleware)

app.include_router(health.router)
app.include_router(webhooks.router)
app.include_router(imports.router)
app.include_router(dashboard.router)
app.include_router(interns.router)
app.include_router(feedback.router)
app.include_router(signals.router)

frontend_dir = Path(settings.frontend_dir)
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
else:
    logger.warning("Frontend directory not found: %s", frontend_dir)
