"""Worker registration and status persistence."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import AttemptStatus, JobAttempt, Worker, WorkerStatus
from app.models import AuditActorType, AuditEntityType, AuditEventType
from app.services.audit_service import record_event
from sqlalchemy import update


def register_worker(
    session: Session,
    *,
    worker_id: uuid.UUID,
    worker_name: str,
    hostname: str,
    process_id: int,
) -> Worker:
    """Stage a new worker record in the caller's transaction."""
    worker = Worker(
        id=worker_id,
        worker_name=worker_name,
        hostname=hostname,
        process_id=process_id,
        status=WorkerStatus.STARTING,
        started_at=datetime.now(timezone.utc),
        last_heartbeat_at=datetime.now(timezone.utc),
    )
    session.add(worker)
    session.flush()
    record_event(session, event_type=AuditEventType.WORKER_REGISTERED, entity_type=AuditEntityType.WORKER, entity_id=worker.id, actor_type=AuditActorType.WORKER, actor_id=worker.id, worker_id=worker.id, details={"hostname": hostname, "process_id": process_id})
    return worker


def set_worker_status(session: Session, worker_id: uuid.UUID, status: WorkerStatus) -> Worker | None:
    """Update one worker status without committing the caller's transaction."""
    worker = session.get(Worker, worker_id)
    if worker is None:
        return None
    worker.status = status
    now = datetime.now(timezone.utc)
    worker.updated_at = now
    if status == WorkerStatus.STOPPED:
        worker.stopped_at = now
        worker.current_job_id = None
    return worker


def heartbeat_worker(session: Session, worker_id: uuid.UUID, heartbeat_at: datetime) -> bool:
    """Record a lightweight heartbeat without loading the Worker row."""
    result = session.execute(
        update(Worker)
        .where(Worker.id == worker_id, Worker.status.in_([WorkerStatus.IDLE, WorkerStatus.BUSY]))
        .values(last_heartbeat_at=heartbeat_at, updated_at=heartbeat_at)
    )
    session.execute(
        update(JobAttempt)
        .where(JobAttempt.worker_id == worker_id, JobAttempt.status == AttemptStatus.RUNNING)
        .values(last_heartbeat_at=heartbeat_at)
    )
    return result.rowcount == 1
