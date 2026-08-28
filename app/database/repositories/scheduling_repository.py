"""PostgreSQL row-locking operations for one-time scheduled jobs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Job, JobStatus


@dataclass(frozen=True, slots=True)
class ScheduledPromotion:
    """Safe event data for a committed scheduled-job promotion."""

    job_id: uuid.UUID
    scheduled_at: datetime
    promoted_at: datetime


def promote_scheduled_jobs(session: Session, *, batch_size: int) -> list[ScheduledPromotion]:
    """Promote only due scheduled jobs under short PostgreSQL row locks."""
    statement = (
        select(Job)
        .where(
            Job.status == JobStatus.SCHEDULED,
            Job.scheduled_at.is_not(None),
            Job.scheduled_at <= func.now(),
        )
        .order_by(Job.scheduled_at.asc(), Job.id.asc())
        .with_for_update(skip_locked=True)
        .limit(batch_size)
    )
    jobs = list(session.scalars(statement))
    promoted_at = datetime.now(timezone.utc)
    promotions: list[ScheduledPromotion] = []
    for job in jobs:
        scheduled_at = job.scheduled_at
        if scheduled_at is None:
            continue
        job.status = JobStatus.PENDING
        job.updated_at = promoted_at
        promotions.append(ScheduledPromotion(job.id, scheduled_at, promoted_at))
    return promotions
