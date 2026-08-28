"""Retry state transition service."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.exc import SQLAlchemyError

from app.database.repositories.retry_repository import RetryOutcome, record_failure
from app.database.session import session_scope
from app.services.retry_policy import RetryPolicy

logger = logging.getLogger("taskforge.retry")


class RetryDatabaseError(RuntimeError):
    """Raised when a retry transition cannot be persisted."""


def handle_task_failure(
    *,
    job_id: uuid.UUID,
    attempt_id: uuid.UUID,
    worker_id: uuid.UUID,
    error: Exception,
    policy: RetryPolicy,
) -> RetryOutcome:
    """Persist one failure and its retry decision in one short transaction."""
    safe_error = f"{type(error).__name__}: task execution failed"
    try:
        with session_scope() as session:
            outcome = record_failure(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                worker_id=worker_id,
                error_message=safe_error,
                error=error,
                policy=policy,
            )
            session.commit()
            return outcome
    except SQLAlchemyError as exc:
        logger.exception("Database error handling failed job %s", job_id)
        raise RetryDatabaseError from exc
