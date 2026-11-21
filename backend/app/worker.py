from __future__ import annotations

import logging
import time
from datetime import date

from .config import settings
from .database import SessionLocal, init_db
from .services.analysis import analyze_pending
from .services.rules import recompute_flags

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("worker")


def main() -> None:
    init_db()
    # Load the NER model once at startup, not on the first survey comment.
    from .services.anonymize import ner_available

    ner_available()
    logger.info("Worker started, provider=%s", settings.llm_provider)
    last_calendar_refresh = None
    while True:
        db = SessionLocal()
        try:
            today = date.today()
            if today != last_calendar_refresh:
                recompute_flags(db, today=today)
                last_calendar_refresh = today
            count = analyze_pending(db, limit=20)
        except Exception:  # noqa: BLE001
            logger.exception("Worker iteration failed")
            count = 0
        finally:
            db.close()
        if count == 0:
            time.sleep(max(0.5, settings.worker_poll_seconds))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Worker stopped")
