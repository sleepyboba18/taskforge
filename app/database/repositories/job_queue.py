"""PostgreSQL row-locking operations for the durable job queue."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import AttemptStatus, Job, JobAttempt, JobDependency, JobStatus, Worker
from app.services.workflow_service import update_workflow_status
from app.models import AuditActorType, AuditEntityType, AuditEventType
from app.services.audit_service import record_event


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """Detached data required to execute a job after its claim commits."""

    job_id: uuid.UUID
    attempt_id: uuid.UUID
    task_type: str
    payload: dict[str, Any]
    worker_id: uuid.UUID


def claim_next_job(session: Session, worker_id: uuid.UUID) -> ClaimedJob | None:
    """Atomically claim the highest-priority eligible job with SKIP LOCKED."""
    dependency_parent = aliased(Job)
    statement = (
        select(Job)
        .where(
            Job.status == JobStatus.PENDING,
            or_(Job.scheduled_at.is_(None), Job.scheduled_at <= func.now()),
            ~exists(
                select(JobDependency.id).join(
                    dependency_parent, dependency_parent.id == JobDependency.depends_on_job_id
                ).where(
                    JobDependency.job_id == Job.id,
                    dependency_parent.status != JobStatus.COMPLETED,
                )
            ),
        )
        .order_by(Job.priority.desc(), Job.created_at.asc(), Job.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = session.scalar(statement)
    if job is None:
        return None

    now = datetime.now(timezone.utc)
    previous_attempt = session.scalar(
        select(func.max(JobAttempt.attempt_number)).where(JobAttempt.job_id == job.id)
    )
    attempt = JobAttempt(
        job_id=job.id,
        attempt_number=(previous_attempt or 0) + 1,
        worker_id=worker_id,
        status=AttemptStatus.RUNNING,
        started_at=now,
        last_heartbeat_at=now,
    )
    previous_status = job.status.value
    job.status = JobStatus.RUNNING
    update_workflow_status(session, job.workflow_id)
    job.worker_id = worker_id
    job.started_at = now
    worker = session.get(Worker, worker_id)
    if worker is not None:
        worker.current_job_id = job.id
    session.add(attempt)
    session.flush()
    record_event(session, event_type=AuditEventType.JOB_STATE_CHANGED, entity_type=AuditEntityType.JOB, entity_id=job.id, actor_type=AuditActorType.WORKER, actor_id=worker_id, worker_id=worker_id, job_id=job.id, workflow_id=job.workflow_id, details={"from_status": previous_status, "to_status": JobStatus.RUNNING.value, "reason": "worker_claimed_job"})
    record_event(session, event_type=AuditEventType.JOB_ATTEMPT_STARTED, entity_type=AuditEntityType.JOB_ATTEMPT, entity_id=attempt.id, actor_type=AuditActorType.WORKER, actor_id=worker_id, worker_id=worker_id, job_id=job.id, workflow_id=job.workflow_id, job_attempt_id=attempt.id, details={"attempt_number": attempt.attempt_number})
    return ClaimedJob(job.id, attempt.id, job.task_type, dict(job.payload), worker_id)
