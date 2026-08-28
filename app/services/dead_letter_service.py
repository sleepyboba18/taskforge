"""Dead-letter management service."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database.repositories.dead_letter_repository import (
    get_dead_letter,
    get_dead_letter_for_update,
    list_dead_letters as repository_list_dead_letters,
)
from app.database.session import session_scope
from app.models import DeadLetterJob, Job, JobStatus
from app.sockets import publish_event
from app.models import AuditEntityType, AuditEventType
from app.services.audit_service import record_current

logger = logging.getLogger("taskforge.dead_letters")


class DeadLetterNotFoundError(RuntimeError):
    """Raised when an active DLQ record does not exist."""


class DeadLetterConflictError(RuntimeError):
    """Raised when a DLQ operation conflicts with current Job state."""


class DeadLetterDatabaseError(RuntimeError):
    """Raised when DLQ persistence fails."""


def get_dead_letter_by_id(dead_letter_id: uuid.UUID) -> DeadLetterJob:
    try:
        with session_scope() as session:
            record = get_dead_letter(session, dead_letter_id)
            if record is None:
                raise DeadLetterNotFoundError
            session.expunge(record)
            return record
    except DeadLetterNotFoundError:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Database error retrieving dead letter %s", dead_letter_id)
        raise DeadLetterDatabaseError from exc


def list_dead_letters(
    *,
    page: int,
    per_page: int,
    job_id: uuid.UUID | None = None,
    task_type: str | None = None,
    recurring_job_id: uuid.UUID | None = None,
) -> tuple[list[DeadLetterJob], int]:
    try:
        with session_scope() as session:
            records, total = repository_list_dead_letters(
                session, page=page, per_page=per_page, job_id=job_id,
                task_type=task_type, recurring_job_id=recurring_job_id,
            )
            for record in records:
                session.expunge(record)
            return records, total
    except SQLAlchemyError as exc:
        logger.exception("Database error listing dead letters")
        raise DeadLetterDatabaseError from exc


def retry_dead_letter(dead_letter_id: uuid.UUID) -> Job:
    """Atomically remove the active DLQ record and requeue its original Job."""
    try:
        with session_scope() as session:
            record = get_dead_letter_for_update(session, dead_letter_id)
            if record is None:
                raise DeadLetterNotFoundError
            job = session.scalar(select(Job).where(Job.id == record.job_id).with_for_update())
            if job is None:
                raise DeadLetterConflictError
            if job.status != JobStatus.FAILED:
                raise DeadLetterConflictError
            session.delete(record)
            record_current(session, event_type=AuditEventType.DLQ_RETRIED, entity_type=AuditEntityType.DLQ, entity_id=record.id, job_id=job.id, workflow_id=job.workflow_id, details={"reason": "manual_retry"})
            record_current(session, event_type=AuditEventType.JOB_RETRIED, entity_type=AuditEntityType.JOB, entity_id=job.id, job_id=job.id, workflow_id=job.workflow_id, details={"retry_source": "DLQ"})
            job.status = JobStatus.PENDING
            job.retry_count = 0
            job.next_retry_at = None
            job.started_at = None
            job.completed_at = None
            job.worker_id = None
            job.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(job)
            session.expunge(job)
    except (DeadLetterNotFoundError, DeadLetterConflictError):
        raise
    except SQLAlchemyError as exc:
        logger.exception("Database error retrying dead letter %s", dead_letter_id)
        raise DeadLetterDatabaseError from exc
    publish_event(
        "job:dead_letter_retry",
        {"job_id": str(job.id), "dead_letter_id": str(dead_letter_id), "status": JobStatus.PENDING.value},
    )
    logger.info("Dead-letter job requeued: job_id=%s dead_letter_id=%s", job.id, dead_letter_id)
    return job


def delete_dead_letter(dead_letter_id: uuid.UUID) -> None:
    """Delete only the DLQ management record, preserving Job history."""
    try:
        with session_scope() as session:
            record = get_dead_letter_for_update(session, dead_letter_id)
            if record is None:
                raise DeadLetterNotFoundError
            session.delete(record)
            record_current(session, event_type=AuditEventType.DLQ_DISCARDED, entity_type=AuditEntityType.DLQ, entity_id=record.id, job_id=record.job_id, details={"reason": "manual_delete"})
            session.commit()
    except DeadLetterNotFoundError:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Database error deleting dead letter %s", dead_letter_id)
        raise DeadLetterDatabaseError from exc
    logger.info("Dead-letter record deleted: %s", dead_letter_id)
