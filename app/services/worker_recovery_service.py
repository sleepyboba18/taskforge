"""Conservative stale-worker and abandoned-job recovery."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database.repositories.dead_letter_repository import create_dead_letter
from app.database.session import session_scope
from app.models import AttemptStatus, Job, JobAttempt, JobStatus, Worker, WorkerStatus
from app.services.retry_policy import RetryPolicy
from app.sockets import publish_event

logger = logging.getLogger("taskforge.worker_recovery")


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    job_id: uuid.UUID
    worker_id: uuid.UUID
    attempt_id: uuid.UUID
    status: JobStatus
    dead_letter_id: uuid.UUID | None


class RecoveryDatabaseError(RuntimeError):
    """Raised when recovery cannot complete its transaction."""


def recover_stale_workers(*, stale_timeout: float, policy: RetryPolicy) -> list[RecoveryOutcome]:
    """Mark stale workers and recover only still-running owned attempts."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_timeout)
    outcomes: list[RecoveryOutcome] = []
    try:
        with session_scope() as session:
            workers = list(
                session.scalars(
                    select(Worker)
                    .where(
                        Worker.status.in_([WorkerStatus.IDLE, WorkerStatus.BUSY]),
                        Worker.last_heartbeat_at.is_not(None),
                        Worker.last_heartbeat_at < cutoff,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for worker in workers:
                # Re-check after locking; a late heartbeat wins over recovery.
                if worker.last_heartbeat_at is None or worker.last_heartbeat_at >= cutoff:
                    continue
                worker.status = WorkerStatus.STALE
                worker.updated_at = datetime.now(timezone.utc)
                attempts = list(
                    session.scalars(
                        select(JobAttempt)
                        .where(
                            JobAttempt.worker_id == worker.id,
                            JobAttempt.status == AttemptStatus.RUNNING,
                        )
                        .with_for_update(skip_locked=True)
                    )
                )
                for attempt in attempts:
                    job = session.scalar(select(Job).where(Job.id == attempt.job_id).with_for_update())
                    if job is None or job.status != JobStatus.RUNNING or job.worker_id != worker.id:
                        continue
                    now = datetime.now(timezone.utc)
                    attempt.status = AttemptStatus.FAILED
                    attempt.finished_at = now
                    attempt.error_message = "Worker heartbeat timed out"
                    attempt.last_heartbeat_at = worker.last_heartbeat_at
                    decision = policy.decide(
                        retry_count=job.retry_count,
                        max_retries=job.max_retries,
                        error=RuntimeError("worker lost"),
                    )
                    job.last_error = "WORKER_LOST: worker heartbeat timed out"
                    job.updated_at = now
                    if decision.should_retry:
                        job.status = JobStatus.RETRYING
                        job.retry_count += 1
                        job.next_retry_at = now + timedelta(seconds=decision.delay_seconds or 0)
                        dead_letter_id = None
                    else:
                        job.status = JobStatus.FAILED
                        job.completed_at = now
                        job.next_retry_at = None
                        record = create_dead_letter(
                            session,
                            job=job,
                            attempt=attempt,
                            error_type="WORKER_LOST",
                            error_message="Worker heartbeat timed out",
                        )
                        dead_letter_id = record.id
                    if worker.current_job_id == job.id:
                        worker.current_job_id = None
                    outcomes.append(RecoveryOutcome(job.id, worker.id, attempt.id, job.status, dead_letter_id))
                if worker.current_job_id is not None and session.get(Job, worker.current_job_id) is None:
                    worker.current_job_id = None
            session.commit()
    except SQLAlchemyError as exc:
        logger.exception("Worker recovery database error")
        raise RecoveryDatabaseError from exc

    for outcome in outcomes:
        payload = {
            "job_id": str(outcome.job_id),
            "worker_id": str(outcome.worker_id),
            "reason": "WORKER_LOST",
            "status": outcome.status.value,
        }
        publish_event("job:recovered", payload)
        if outcome.dead_letter_id is not None:
            publish_event(
                "job:dead_lettered",
                {
                    "job_id": str(outcome.job_id),
                    "dead_letter_id": str(outcome.dead_letter_id),
                    "status": JobStatus.FAILED.value,
                },
            )
    return outcomes
