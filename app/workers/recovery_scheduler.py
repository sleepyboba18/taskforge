"""Managed process for stale worker and abandoned job recovery."""

from __future__ import annotations

import logging

from app.database.session import dispose_database, initialize_database
from app.services.retry_policy import RetryPolicy
from app.services.worker_recovery_service import RecoveryDatabaseError, recover_stale_workers

logger = logging.getLogger("taskforge.recovery_scheduler")


def run_recovery_scheduler(
    *, database_url: str, poll_interval: float, stale_timeout: float,
    retry_base_delay: float, retry_max_delay: float, shutdown_event,
) -> None:
    """Poll PostgreSQL for stale workers without executing tasks."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    dispose_database()
    initialize_database(database_url)
    policy = RetryPolicy(retry_base_delay, retry_max_delay)
    logger.info("Recovery scheduler started")
    while not shutdown_event.is_set():
        try:
            outcomes = recover_stale_workers(stale_timeout=stale_timeout, policy=policy)
            if outcomes:
                logger.info("Recovered %d abandoned job(s)", len(outcomes))
        except RecoveryDatabaseError:
            logger.exception("Recovery scheduler database error")
        shutdown_event.wait(poll_interval)
    logger.info("Recovery scheduler stopped")
