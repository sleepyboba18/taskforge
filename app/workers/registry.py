"""Worker registration and status persistence."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Worker, WorkerStatus


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
    return worker
