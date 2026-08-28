"""Persistence operations for jobs."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import Job, JobStatus


def add_job(session: Session, job: Job) -> Job:
    """Stage a job for insertion in the caller's transaction."""
    session.add(job)
    session.flush()
    return job


def get_job(session: Session, job_id: uuid.UUID) -> Job | None:
    """Find one job by UUID."""
    return session.get(Job, job_id)


def get_job_for_update(session: Session, job_id: uuid.UUID) -> Job | None:
    """Find one job with a row lock for a state transition."""
    statement = select(Job).where(Job.id == job_id).with_for_update()
    return session.scalar(statement)


def list_jobs(
    session: Session,
    *,
    page: int,
    per_page: int,
    status: JobStatus | None = None,
    task_type: str | None = None,
    priority: int | None = None,
) -> tuple[list[Job], int]:
    """Query filtered jobs and count rows in the database."""
    filters: list[Any] = []
    if status is not None:
        filters.append(Job.status == status)
    if task_type is not None:
        filters.append(Job.task_type == task_type)
    if priority is not None:
        filters.append(Job.priority == priority)

    count_statement = select(func.count()).select_from(Job).where(*filters)
    total = session.scalar(count_statement) or 0

    statement: Select[tuple[Job]] = (
        select(Job)
        .where(*filters)
        .order_by(Job.created_at.desc(), Job.id.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    return list(session.scalars(statement)), total
