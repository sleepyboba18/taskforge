"""Persistence operations for dead-letter records."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DeadLetterJob, Job, JobAttempt, JobStatus


def create_dead_letter(
    session: Session,
    *,
    job: Job,
    attempt: JobAttempt,
    error_type: str,
    error_message: str,
) -> DeadLetterJob:
    """Create one DLQ record for a terminal Job within the caller transaction."""
    existing = session.scalar(select(DeadLetterJob).where(DeadLetterJob.job_id == job.id))
    if existing is not None:
        return existing
    record = DeadLetterJob(
        job_id=job.id,
        task_type=job.task_type,
        payload=dict(job.payload),
        error_type=error_type,
        error_message=error_message,
        attempt_count=session.scalar(
            select(func.count()).select_from(JobAttempt).where(JobAttempt.job_id == job.id)
        )
        or 1,
        last_attempt_id=attempt.id,
        failed_at=datetime.now(timezone.utc),
        source="TASK_EXECUTION",
        recurring_job_id=job.recurring_job_id,
    )
    session.add(record)
    session.flush()
    return record


def get_dead_letter_for_update(session: Session, dead_letter_id: uuid.UUID) -> DeadLetterJob | None:
    """Lock a DLQ record for an administrative state transition."""
    return session.scalar(
        select(DeadLetterJob).where(DeadLetterJob.id == dead_letter_id).with_for_update()
    )


def get_dead_letter(session: Session, dead_letter_id: uuid.UUID) -> DeadLetterJob | None:
    return session.get(DeadLetterJob, dead_letter_id)


def list_dead_letters(
    session: Session,
    *,
    page: int,
    per_page: int,
    job_id: uuid.UUID | None = None,
    task_type: str | None = None,
    recurring_job_id: uuid.UUID | None = None,
) -> tuple[list[DeadLetterJob], int]:
    filters = []
    if job_id is not None:
        filters.append(DeadLetterJob.job_id == job_id)
    if task_type is not None:
        filters.append(DeadLetterJob.task_type == task_type)
    if recurring_job_id is not None:
        filters.append(DeadLetterJob.recurring_job_id == recurring_job_id)
    total = session.scalar(select(func.count()).select_from(DeadLetterJob).where(*filters)) or 0
    records = list(
        session.scalars(
            select(DeadLetterJob)
            .where(*filters)
            .order_by(DeadLetterJob.failed_at.desc(), DeadLetterJob.id.asc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    )
    return records, total
