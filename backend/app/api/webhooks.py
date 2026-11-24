from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..adapters.yandex_forms import YANDEX_SOURCES, AdapterError, normalize_source, parse_payload
from ..auth import check_webhook_secret
from ..config import settings
from ..database import SessionLocal, get_db
from ..schemas import WebhookResult
from ..services.analysis import analyze_answer_by_id
from ..auth import app_env
from ..services.ingest import DemoInternError, intern_exists, log_ingest_event, upsert_answer
from ..services.rules import recompute_flags

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

# CSV and demo answers have their own entry points; a webhook must not write
# into their idempotency namespace.
WEBHOOK_SOURCES = {"generic", *YANDEX_SOURCES}


def unknown_intern_policy() -> str:
    configured = (settings.webhook_unknown_interns or "").strip().lower()
    if configured in {"create", "hold"}:
        return configured
    return "create" if app_env() in {"dev", "demo"} else "hold"


def run_background_analysis(answer_id: int) -> None:
    db = SessionLocal()
    try:
        analyze_answer_by_id(answer_id)
        recompute_flags(db)
    except Exception:  # noqa: BLE001 - a bad answer must not kill the process
        logger.exception("Background analysis failed for answer %s", answer_id)
    finally:
        db.close()


@router.post(
    "/api/webhooks/{source}",
    response_model=WebhookResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_webhook(
    source: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
    db: Session = Depends(get_db),
) -> WebhookResult:
    check_webhook_secret(request, x_webhook_secret)
    source_key = source.strip().lower().replace("-", "_")
    if source_key == "demo_feedback":
        raise HTTPException(status_code=422, detail="Этот источник зарезервирован для демо-анкет.")
    if source_key not in WEBHOOK_SOURCES:
        raise HTTPException(status_code=404, detail="Неизвестный источник вебхука.")
    source = "generic" if source_key == "generic" else source

    try:
        payload: Any = await request.json()
    except Exception:  # noqa: BLE001
        body = await request.body()
        payload = {"raw": body.decode("utf-8", errors="replace")[:20_000]}
    if not isinstance(payload, dict):
        payload = {"payload": payload}

    try:
        canonical = parse_payload(source, payload)
    except Exception as exc:  # noqa: BLE001 - preserve the raw event for manual mapping
        if isinstance(exc, AdapterError):
            detail = str(exc)
        elif isinstance(exc, ValidationError):
            fields = {
                ".".join(map(str, error["loc"]))
                for error in exc.errors(include_input=False, include_context=False, include_url=False)
            }
            detail = f"Проверьте поля анкеты: {', '.join(sorted(fields))}."
        else:
            detail = "Проверьте формат полей и отчётного периода анкеты."
        logger.warning("Cannot parse webhook: %s", type(exc).__name__)
        log_ingest_event(
            db,
            source=source,
            payload=payload,
            status="parse_error",
            error=detail[:2000],
            external_id=str(payload.get("id") or payload.get("answer_id") or "") or None,
        )
        return WebhookResult(
            status="stored_unparsed",
            created=False,
            message=detail[:500],
        )

    canonical.source = normalize_source(source)
    if unknown_intern_policy() == "hold" and not intern_exists(db, canonical.intern_external_id):
        # A typo or a forged ID must not create a person in the HR dashboard.
        log_ingest_event(
            db,
            source=source,
            payload=payload,
            status="unknown_intern",
            error=f"Стажёр {canonical.intern_external_id!r} не найден в справочнике",
            external_id=canonical.external_answer_id,
        )
        return WebhookResult(
            status="held",
            created=False,
            message="Стажёра нет в справочнике: ответ сохранён для ручного сопоставления.",
        )
    try:
        answer, created = upsert_answer(db, canonical, raw_payload=payload)
    except DemoInternError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()

    if created and settings.analyze_in_background:
        background_tasks.add_task(run_background_analysis, answer.id)

    return WebhookResult(
        status="accepted",
        created=created,
        answer_id=answer.id,
        message=None if created else "Ответ уже был получен ранее",
    )
