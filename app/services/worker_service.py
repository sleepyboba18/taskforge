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


def list_workers(
    *, status: WorkerStatus | None = None, page: int = 1, per_page: int = 20
) -> tuple[list[Worker], int]:
    try:
        with session_scope() as session:
            filters = []
            if status is not None:
                filters.append(Worker.status == status)
            total = session.scalar(select(func.count()).select_from(Worker).where(*filters)) or 0
            workers = list(
                session.scalars(
                    select(Worker)
                    .where(*filters)
                    .order_by(Worker.created_at.desc(), Worker.id.asc())
                    .offset((page - 1) * per_page)
                    .limit(per_page)
                )
            )
            for worker in workers:
                session.expunge(worker)
            return workers, total
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
            total, healthy, stale = session.execute(
                select(
                    func.count().label("total"),
                    func.count()
                    .filter(
                        Worker.status.in_([WorkerStatus.IDLE, WorkerStatus.BUSY]),
                        Worker.last_heartbeat_at >= cutoff,
                    )
                    .label("healthy"),
                    func.count()
                    .filter(Worker.status == WorkerStatus.STALE)
                    .label("stale"),
                )
            ).one()
            return {
                "healthy": healthy > 0,
                "total_workers": total,
                "running_workers": healthy,
                "stale_workers": stale,
            }
    except SQLAlchemyError as exc:
        logger.exception("Database error reading worker health")
        raise WorkerDatabaseError from exc
