"""Read-only worker health service."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.database.session import session_scope
from app.models import Worker, WorkerStatus

logger = logging.getLogger("taskforge.workers")


class WorkerDatabaseError(RuntimeError):
    """Raised when worker health data cannot be read."""


def list_workers(*, status: WorkerStatus | None = None) -> list[Worker]:
    try:
        with session_scope() as session:
            statement = select(Worker).order_by(Worker.created_at.desc())
            if status is not None:
                statement = statement.where(Worker.status == status)
            workers = list(session.scalars(statement))
            for worker in workers:
                session.expunge(worker)
            return workers
    except SQLAlchemyError as exc:
        logger.exception("Database error listing workers")
        raise WorkerDatabaseError from exc


def get_worker(worker_id: uuid.UUID) -> Worker | None:
    try:
        with session_scope() as session:
            worker = session.get(Worker, worker_id)
            if worker is not None:
                session.expunge(worker)
            return worker
    except SQLAlchemyError as exc:
        logger.exception("Database error retrieving worker %s", worker_id)
        raise WorkerDatabaseError from exc


def worker_health(*, stale_timeout: float) -> dict[str, int | bool]:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_timeout)
    try:
        with session_scope() as session:
            total = session.scalar(select(func.count()).select_from(Worker)) or 0
            healthy = session.scalar(
                select(func.count()).select_from(Worker).where(
                    Worker.status.in_([WorkerStatus.IDLE, WorkerStatus.BUSY]),
                    Worker.last_heartbeat_at >= cutoff,
                )
            ) or 0
            stale = session.scalar(
                select(func.count()).select_from(Worker).where(Worker.status == WorkerStatus.STALE)
            ) or 0
            return {
                "healthy": healthy > 0,
                "total_workers": total,
                "running_workers": healthy,
                "stale_workers": stale,
            }
    except SQLAlchemyError as exc:
        logger.exception("Database error reading worker health")
        raise WorkerDatabaseError from exc
