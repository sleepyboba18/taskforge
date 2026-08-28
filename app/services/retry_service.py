"""Retry state transition service."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.exc import SQLAlchemyError

from app.database.repositories.retry_repository import RetryOutcome, record_failure
from app.database.session import session_scope
from app.models import Job, JobStatus
from app.services.retry_policy import RetryPolicy
from app.services.dependency_service import propagate_dependency_failure
from app.services.workflow_service import update_workflow_status

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
    max_dependency_propagation_depth: int = 50,
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
            if outcome.status == JobStatus.FAILED:
                propagate_dependency_failure(session, job_id, max_depth=max_dependency_propagation_depth)
            job = session.get(Job, job_id)
            update_workflow_status(session, job.workflow_id if job else None)
            session.commit()
            return outcome
    except SQLAlchemyError as exc:
        logger.exception("Database error handling failed job %s", job_id)
        raise RetryDatabaseError from exc
