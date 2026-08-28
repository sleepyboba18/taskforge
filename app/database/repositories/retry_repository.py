"""Transactional retry failure and promotion operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AttemptStatus, Job, JobAttempt, JobStatus, Worker, WorkerStatus
from app.database.repositories.dead_letter_repository import create_dead_letter
from app.services.retry_policy import RetryPolicy
from app.workers.registry import set_worker_status
from app.models import AuditActorType, AuditEntityType, AuditEventType
from app.services.audit_service import record_event


@dataclass(frozen=True, slots=True)
class RetryOutcome:
    """Committed result of handling one failed attempt."""

    job_id: uuid.UUID
    attempt_id: uuid.UUID
    worker_id: uuid.UUID
    status: JobStatus
    retry_count: int
    max_retries: int
    next_retry_at: datetime | None
    attempt_number: int
    dead_letter_id: uuid.UUID | None = None


def record_failure(
    session: Session,
    *,
    job_id: uuid.UUID,
    attempt_id: uuid.UUID,
    worker_id: uuid.UUID,
    error_message: str,
    error: Exception,
    policy: RetryPolicy,
) -> RetryOutcome:
    """Atomically fail the attempt and schedule or finalize its job."""
    job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    attempt = session.get(JobAttempt, attempt_id)
    if job is None or attempt is None:
        raise RuntimeError("Claimed job or attempt no longer exists.")
    if job.status != JobStatus.RUNNING or attempt.status != AttemptStatus.RUNNING:
        raise RuntimeError("Job is not in a retryable running state.")

    now = datetime.now(timezone.utc)
    attempt.status = AttemptStatus.FAILED
    attempt.finished_at = now
    attempt.error_message = error_message
    record_event(session, event_type=AuditEventType.JOB_ATTEMPT_FAILED, entity_type=AuditEntityType.JOB_ATTEMPT, entity_id=attempt.id, actor_type=AuditActorType.WORKER, actor_id=worker_id, worker_id=worker_id, job_id=job.id, workflow_id=job.workflow_id, job_attempt_id=attempt.id, details={"attempt_number": attempt.attempt_number, "error_type": type(error).__name__, "retryable": job.retry_count < job.max_retries})
    decision = policy.decide(retry_count=job.retry_count, max_retries=job.max_retries, error=error)
    job.last_error = error_message
    job.updated_at = now
    previous_status = JobStatus.RUNNING
    if decision.should_retry:
        job.status = JobStatus.RETRYING
        job.retry_count += 1
        job.next_retry_at = now + timedelta(seconds=decision.delay_seconds or 0)
        next_retry_at = job.next_retry_at
    else:
        job.status = JobStatus.FAILED
        job.completed_at = now
        job.next_retry_at = None
        next_retry_at = None
        record_event(session, event_type=AuditEventType.JOB_STATE_CHANGED, entity_type=AuditEntityType.JOB, entity_id=job.id, actor_type=AuditActorType.WORKER, actor_id=worker_id, worker_id=worker_id, job_id=job.id, workflow_id=job.workflow_id, details={"from_status": previous_status.value, "to_status": job.status.value, "reason": "attempt_failed"})
        dead_letter = create_dead_letter(
            session,
            job=job,
            attempt=attempt,
            error_type=type(error).__name__,
            error_message=error_message,
        )
        dead_letter_id = dead_letter.id
        record_event(session, event_type=AuditEventType.JOB_FAILED, entity_type=AuditEntityType.JOB, entity_id=job.id, actor_type=AuditActorType.WORKER, actor_id=worker_id, worker_id=worker_id, job_id=job.id, workflow_id=job.workflow_id)
        record_event(session, event_type=AuditEventType.DLQ_ENTERED, entity_type=AuditEntityType.DLQ, entity_id=dead_letter.id, actor_type=AuditActorType.SYSTEM, worker_id=worker_id, job_id=job.id, workflow_id=job.workflow_id, details={"attempt_number": attempt.attempt_number})
    if decision.should_retry:
        record_event(session, event_type=AuditEventType.JOB_STATE_CHANGED, entity_type=AuditEntityType.JOB, entity_id=job.id, actor_type=AuditActorType.WORKER, actor_id=worker_id, worker_id=worker_id, job_id=job.id, workflow_id=job.workflow_id, details={"from_status": previous_status.value, "to_status": job.status.value, "reason": "retry_scheduled"})
        record_event(session, event_type=AuditEventType.JOB_RETRIED, entity_type=AuditEntityType.JOB, entity_id=job.id, actor_type=AuditActorType.WORKER, actor_id=worker_id, worker_id=worker_id, job_id=job.id, workflow_id=job.workflow_id, details={"retry_source": "AUTOMATIC", "retry_number": job.retry_count})
        dead_letter_id = None
    set_worker_status(session, worker_id, WorkerStatus.IDLE)
    worker = session.get(Worker, worker_id)
    if worker is not None:
        worker.current_job_id = None
    return RetryOutcome(
        job_id=job.id,
        attempt_id=attempt.id,
        worker_id=worker_id,
        status=job.status,
        retry_count=job.retry_count,
        max_retries=job.max_retries,
        next_retry_at=next_retry_at,
        attempt_number=attempt.attempt_number,
        dead_letter_id=dead_letter_id,
    )


def promote_retrying_jobs(session: Session, *, batch_size: int) -> list[RetryOutcome]:
    """Promote one bounded batch of due retries under PostgreSQL row locks."""
    statement = (
        select(Job)
        .where(
            Job.status == JobStatus.RETRYING,
            Job.next_retry_at.is_not(None),
            Job.next_retry_at <= func.now(),
        )
        .order_by(Job.next_retry_at.asc(), Job.id.asc())
        .with_for_update(skip_locked=True)
        .limit(batch_size)
    )
    jobs = list(session.scalars(statement))
    outcomes: list[RetryOutcome] = []
    for job in jobs:
        job.status = JobStatus.PENDING
        job.next_retry_at = None
        job.updated_at = datetime.now(timezone.utc)
        outcomes.append(
            RetryOutcome(
                job_id=job.id,
                attempt_id=uuid.UUID(int=0),
                worker_id=job.worker_id or uuid.UUID(int=0),
                status=JobStatus.PENDING,
                retry_count=job.retry_count,
                max_retries=job.max_retries,
                next_retry_at=None,
                attempt_number=0,
            )
        )
    return outcomes
