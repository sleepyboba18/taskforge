"""Business operations for durable job submission and cancellation."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.database.repositories.job_repository import (
    add_job,
    get_job,
    get_job_for_update,
    list_jobs as repository_list_jobs,
)
from app.database.session import session_scope
from app.models import Job, JobStatus
from app.sockets import publish_event

logger = logging.getLogger("taskforge.jobs")

CANCELLABLE_STATUSES = {JobStatus.PENDING, JobStatus.SCHEDULED, JobStatus.RETRYING}


class JobServiceError(RuntimeError):
    """Base error for expected job service failures."""


class JobNotFoundError(JobServiceError):
    """Raised when a requested job does not exist."""


class JobStateConflictError(JobServiceError):
    """Raised when a job cannot make the requested state transition."""

    def __init__(self, status: JobStatus):
        self.status = status
        super().__init__(f"Job cannot be cancelled from status {status.value}.")


class JobDatabaseError(JobServiceError):
    """Raised when persistence fails without exposing database details."""


def create_job(
    *,
    name: str,
    task_type: str,
    payload: dict[str, Any],
    priority: int,
    max_retries: int,
    scheduled_at: datetime | None,
) -> Job:
    """Persist a job and emit its notification only after commit."""
    now = datetime.now(timezone.utc)
    status = JobStatus.SCHEDULED if scheduled_at and scheduled_at > now else JobStatus.PENDING
    job = Job(
        name=name,
        task_type=task_type,
        payload=payload,
        priority=priority,
        max_retries=max_retries,
        scheduled_at=scheduled_at,
        status=status,
    )
    try:
        with session_scope() as session:
            add_job(session, job)
            session.commit()
    except SQLAlchemyError as exc:
        logger.exception("Database error while submitting job")
        raise JobDatabaseError from exc

    logger.info("Job submitted: %s", job.id)
    _emit("job:created", _event_payload(job))
    if job.status == JobStatus.SCHEDULED:
        _emit(
            "job:scheduled",
            {
                "id": str(job.id),
                "status": job.status.value,
                "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
            },
        )
    return job


def get_job_by_id(job_id: uuid.UUID) -> Job:
    """Return a job or raise a controlled not-found error."""
    try:
        with session_scope() as session:
            job = get_job(session, job_id)
            if job is None:
                raise JobNotFoundError
            session.expunge(job)
            return job
    except JobNotFoundError:
        logger.info("Job not found: %s", job_id)
        raise
    except SQLAlchemyError as exc:
        logger.exception("Database error while retrieving job %s", job_id)
        raise JobDatabaseError from exc


def list_jobs(
    *,
    page: int,
    per_page: int,
    status: JobStatus | None = None,
    task_type: str | None = None,
    priority: int | None = None,
) -> tuple[list[Job], int]:
    """Return one database-paginated page and its database count."""
    try:
        with session_scope() as session:
            jobs, total = repository_list_jobs(
                session,
                page=page,
                per_page=per_page,
                status=status,
                task_type=task_type,
                priority=priority,
            )
            for job in jobs:
                session.expunge(job)
            return jobs, total
    except SQLAlchemyError as exc:
        logger.exception("Database error while listing jobs")
        raise JobDatabaseError from exc


def cancel_job(job_id: uuid.UUID) -> Job:
    """Lock and cancel an eligible job atomically."""
    try:
        with session_scope() as session:
            job = get_job_for_update(session, job_id)
            if job is None:
                raise JobNotFoundError
            if job.status not in CANCELLABLE_STATUSES:
                raise JobStateConflictError(job.status)
            job.status = JobStatus.CANCELLED
            session.commit()
            session.refresh(job)
            session.expunge(job)
    except (JobNotFoundError, JobStateConflictError):
        raise
    except SQLAlchemyError as exc:
        logger.exception("Database error while cancelling job %s", job_id)
        raise JobDatabaseError from exc

    logger.info("Job cancelled: %s", job_id)
    _emit("job:cancelled", {"id": str(job.id), "status": job.status.value})
    return job


def _event_payload(job: Job) -> dict[str, Any]:
    """Build a minimal notification payload without the task payload."""
    return {
        "id": str(job.id),
        "name": job.name,
        "task_type": job.task_type,
        "status": job.status.value,
        "priority": job.priority,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
    }


def _emit(event: str, payload: dict[str, Any]) -> None:
    """Keep notification failures from changing a committed database result."""
    publish_event(event, payload)
