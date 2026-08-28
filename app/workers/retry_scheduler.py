"""Database-backed retry promotion scheduler."""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from app.database.repositories.retry_repository import promote_retrying_jobs
from app.database.session import dispose_database, initialize_database, session_scope
from app.models import JobStatus
from app.sockets import publish_event

logger = logging.getLogger("taskforge.retry_scheduler")


def run_retry_scheduler(*, database_url: str, poll_interval: float, batch_size: int, shutdown_event) -> None:
    """Promote due retries without executing tasks or holding long transactions."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    dispose_database()
    initialize_database(database_url)
    while not shutdown_event.is_set():
        try:
            with session_scope() as session:
                outcomes = promote_retrying_jobs(session, batch_size=batch_size)
                session.commit()
            for outcome in outcomes:
                publish_event(
                    "job:retry_ready",
                    {"id": str(outcome.job_id), "status": JobStatus.PENDING.value, "retry_count": outcome.retry_count},
                )
                logger.info("Retry promoted to pending: %s", outcome.job_id)
        except SQLAlchemyError:
            logger.exception("Retry scheduler database error")
        shutdown_event.wait(poll_interval)
