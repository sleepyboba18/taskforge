"""Business operations for recurring job definitions."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database.repositories.recurring_repository import GeneratedExecution, generate_due_execution
from app.database.session import session_scope
from app.models import RecurringJob
from app.services.recurring_schedule_service import ScheduleValidationError, next_occurrence, validate_schedule
from app.sockets import publish_event

logger = logging.getLogger("taskforge.recurring_jobs")


class RecurringJobNotFoundError(RuntimeError):
    """Raised when a recurring definition does not exist."""


class RecurringJobDatabaseError(RuntimeError):
    """Raised when recurring persistence fails."""


def create_recurring_job(*, name: str, task_type: str, payload: dict[str, Any], priority: int, max_retries: int, schedule: str, timezone_name: str) -> RecurringJob:
    """Validate and persist a recurring definition with its first future run."""
    validate_schedule(schedule)
    now = datetime.now(timezone.utc)
    first_run = next_occurrence(schedule, timezone_name, now)
    recurring = RecurringJob(
        name=name, task_type=task_type, payload=payload, priority=priority,
        max_retries=max_retries, schedule_expression=schedule,
        timezone=timezone_name, enabled=True, next_run_at=first_run,
    )
    try:
        with session_scope() as session:
            session.add(recurring)
            session.commit()
    except SQLAlchemyError as exc:
        logger.exception("Database error creating recurring job")
        raise RecurringJobDatabaseError from exc
    publish_event("recurring_job:created", recurring_event(recurring))
    return recurring


def get_recurring_job(recurring_job_id: uuid.UUID) -> RecurringJob:
    try:
        with session_scope() as session:
            recurring = session.get(RecurringJob, recurring_job_id)
            if recurring is None:
                raise RecurringJobNotFoundError
            session.expunge(recurring)
            return recurring
    except RecurringJobNotFoundError:
        raise
    except SQLAlchemyError as exc:
        raise RecurringJobDatabaseError from exc


def list_recurring_jobs(*, enabled: bool | None = None) -> list[RecurringJob]:
    try:
        with session_scope() as session:
            statement = select(RecurringJob).order_by(RecurringJob.created_at.desc())
            if enabled is not None:
                statement = statement.where(RecurringJob.enabled == enabled)
            jobs = list(session.scalars(statement))
            for job in jobs:
                session.expunge(job)
            return jobs
    except SQLAlchemyError as exc:
        raise RecurringJobDatabaseError from exc


def set_recurring_enabled(recurring_job_id: uuid.UUID, enabled: bool) -> RecurringJob:
    try:
        with session_scope() as session:
            recurring = session.scalar(select(RecurringJob).where(RecurringJob.id == recurring_job_id).with_for_update())
            if recurring is None:
                raise RecurringJobNotFoundError
            recurring.enabled = enabled
            if enabled:
                recurring.next_run_at = next_occurrence(
                    recurring.schedule_expression, recurring.timezone, datetime.now(timezone.utc)
                )
            recurring.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(recurring)
            session.expunge(recurring)
    except RecurringJobNotFoundError:
        raise
    except SQLAlchemyError as exc:
        raise RecurringJobDatabaseError from exc
    publish_event(f"recurring_job:{'enabled' if enabled else 'disabled'}", recurring_event(recurring))
    return recurring


def generate_due_recurring_job(recurring_job_id: uuid.UUID, now: datetime | None = None) -> GeneratedExecution | None:
    now = now or datetime.now(timezone.utc)
    try:
        with session_scope() as session:
            generated = generate_due_execution(session, recurring_job_id, now)
            session.commit()
            return generated
    except SQLAlchemyError as exc:
        raise RecurringJobDatabaseError from exc


def recurring_event(recurring: RecurringJob) -> dict[str, Any]:
    return {
        "id": str(recurring.id), "name": recurring.name, "task_type": recurring.task_type,
        "schedule": recurring.schedule_expression, "timezone": recurring.timezone,
        "enabled": recurring.enabled, "next_run_at": recurring.next_run_at.isoformat(),
    }
