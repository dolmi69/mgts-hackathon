from __future__ import annotations

import logging
import time

from .config import settings
from .database import SessionLocal, init_db
from .services.analysis import analyze_pending

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("worker")


def main() -> None:
    init_db()
    logger.info("Worker started, provider=%s", settings.llm_provider)
    while True:
        db = SessionLocal()
        try:
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
