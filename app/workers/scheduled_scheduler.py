"""Database-backed one-time scheduled-job promotion process."""

from __future__ import annotations

import logging

from app.database.session import dispose_database, initialize_database
from app.services.scheduling_service import SchedulingDatabaseError, promote_due_jobs
from app.sockets import publish_event

logger = logging.getLogger("taskforge.scheduled_scheduler")


def run_scheduled_scheduler(*, database_url: str, poll_interval: float, batch_size: int, shutdown_event) -> None:
    """Promote due scheduled jobs; task execution remains the worker's concern."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    dispose_database()
    initialize_database(database_url)
    logger.info("Scheduled-job scheduler started")
    while not shutdown_event.is_set():
        try:
            promotions = promote_due_jobs(batch_size=batch_size)
            for promotion in promotions:
                lag = (promotion.promoted_at - promotion.scheduled_at).total_seconds()
                publish_event(
                    "job:scheduled_ready",
                    {
                        "id": str(promotion.job_id),
                        "status": "PENDING",
                        "scheduled_at": promotion.scheduled_at.isoformat(),
                    },
                )
                logger.info("Scheduled job promoted: job_id=%s schedule_lag=%.3f", promotion.job_id, lag)
        except SchedulingDatabaseError:
            logger.exception("Scheduled-job scheduler database error")
        shutdown_event.wait(poll_interval)
    logger.info("Scheduled-job scheduler stopped")
