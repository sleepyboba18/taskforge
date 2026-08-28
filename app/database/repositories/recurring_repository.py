"""Atomic recurring execution generation operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.models import Job, JobStatus, RecurringJob
from app.services.recurring_schedule_service import next_occurrence
from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class GeneratedExecution:
    """Committed metadata for a generated normal Job."""

    recurring_job_id: uuid.UUID
    job_id: uuid.UUID
    scheduled_for: datetime


def generate_due_execution(session: Session, recurring_job_id: uuid.UUID, now: datetime) -> GeneratedExecution | None:
    """Lock one definition and atomically create at most its current execution."""
    recurring = session.scalar(
        select(RecurringJob).where(RecurringJob.id == recurring_job_id).with_for_update(skip_locked=True)
    )
    if recurring is None or not recurring.enabled or recurring.next_run_at > now:
        return None

    scheduled_for = recurring.next_run_at
    job = Job(
        name=recurring.name,
        task_type=recurring.task_type,
        payload=dict(recurring.payload),
        priority=recurring.priority,
        max_retries=recurring.max_retries,
        status=JobStatus.PENDING,
        recurring_job_id=recurring.id,
    )
    session.add(job)
    next_run = next_occurrence(recurring.schedule_expression, recurring.timezone, scheduled_for)
    while next_run <= now:
        next_run = next_occurrence(recurring.schedule_expression, recurring.timezone, next_run)
    recurring.last_run_at = scheduled_for
    recurring.next_run_at = next_run
    recurring.updated_at = datetime.now(timezone.utc)
    session.flush()
    return GeneratedExecution(recurring.id, job.id, scheduled_for)


def due_recurring_ids(session: Session, *, limit: int, now: datetime) -> list[uuid.UUID]:
    """Find due definitions; generation itself locks each row atomically."""
    statement = (
        select(RecurringJob.id)
        .where(RecurringJob.enabled.is_(True), RecurringJob.next_run_at <= now)
        .order_by(RecurringJob.next_run_at.asc(), RecurringJob.id.asc())
        .limit(limit)
    )
    return list(session.scalars(statement))
