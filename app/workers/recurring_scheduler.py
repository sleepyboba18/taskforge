"""Database-backed recurring cron execution generator."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from app.database.repositories.recurring_repository import due_recurring_ids
from app.database.session import dispose_database, initialize_database, session_scope
from app.services.recurring_job_service import (
    RecurringJobDatabaseError,
    generate_due_recurring_job,
)
from app.sockets import publish_event

logger = logging.getLogger("taskforge.recurring_scheduler")


def run_recurring_scheduler(*, database_url: str, poll_interval: float, batch_size: int, shutdown_event) -> None:
    """Generate normal pending Jobs for due recurring definitions only."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    dispose_database()
    initialize_database(database_url)
    logger.info("Recurring scheduler started")
    while not shutdown_event.is_set():
        now = datetime.now(timezone.utc)
        try:
            with session_scope() as session:
                recurring_ids = due_recurring_ids(session, limit=batch_size, now=now)
            for recurring_id in recurring_ids:
                try:
                    generated = generate_due_recurring_job(recurring_id, now=now)
                except RecurringJobDatabaseError:
                    logger.exception("Database error generating recurring job %s", recurring_id)
                    continue
                if generated is None:
                    continue
                publish_event(
                    "recurring_job:execution_created",
                    {
                        "recurring_job_id": str(generated.recurring_job_id),
                        "job_id": str(generated.job_id),
                        "scheduled_for": generated.scheduled_for.isoformat(),
                    },
                )
                logger.info(
                    "Recurring execution created: recurring_job_id=%s job_id=%s scheduled_for=%s",
                    generated.recurring_job_id,
                    generated.job_id,
                    generated.scheduled_for,
                )
        except SQLAlchemyError:
            logger.exception("Recurring scheduler database error")
        shutdown_event.wait(poll_interval)
    logger.info("Recurring scheduler stopped")
