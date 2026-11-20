from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, status
from sqlalchemy.orm import Session

from ..adapters.yandex_forms import parse_payload
from ..auth import check_webhook_secret
from ..config import settings
from ..database import SessionLocal, get_db
from ..schemas import WebhookResult
from ..services.analysis import analyze_answer_by_id
from ..services.ingest import log_ingest_event, upsert_answer
from ..services.rules import recompute_flags

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


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
    secret: str | None = Query(default=None),
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
    db: Session = Depends(get_db),
) -> WebhookResult:
    check_webhook_secret(secret, x_webhook_secret)

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
        logger.warning("Cannot parse webhook from %s: %s", source, exc)
        log_ingest_event(
            db,
            source=source,
            payload=payload,
            status="parse_error",
            error=str(exc)[:2000],
            external_id=str(payload.get("id") or payload.get("answer_id") or "") or None,
        )
        return WebhookResult(
            status="stored_unparsed",
            created=False,
            message=str(exc)[:500],
        )

    canonical.source = source
    answer, created = upsert_answer(db, canonical, raw_payload=payload)
    db.commit()

    if created and settings.analyze_in_background:
        background_tasks.add_task(run_background_analysis, answer.id)

    return WebhookResult(
        status="accepted",
        created=created,
        answer_id=answer.id,
        message=None if created else "Ответ уже был получен ранее",
    )
