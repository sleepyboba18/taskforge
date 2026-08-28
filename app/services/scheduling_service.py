"""Service boundary for scheduled-job promotion."""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from app.database.repositories.scheduling_repository import ScheduledPromotion, promote_scheduled_jobs
from app.database.session import session_scope

logger = logging.getLogger("taskforge.scheduling")


class SchedulingDatabaseError(RuntimeError):
    """Raised when scheduled-job promotion cannot be persisted."""


def promote_due_jobs(*, batch_size: int) -> list[ScheduledPromotion]:
    """Promote a bounded batch and return only committed promotions."""
    try:
        with session_scope() as session:
            promotions = promote_scheduled_jobs(session, batch_size=batch_size)
            session.commit()
            return promotions
    except SQLAlchemyError as exc:
        logger.exception("Database error promoting scheduled jobs")
        raise SchedulingDatabaseError from exc
