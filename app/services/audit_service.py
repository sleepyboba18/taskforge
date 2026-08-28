"""Append-only PostgreSQL audit event service."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from flask import g, has_request_context

from app.database.session import session_scope
from app.models import AuditActorType, AuditEntityType, AuditEvent, AuditEventType


def record_current(session: Session, **kwargs: Any) -> AuditEvent:
    """Record an event using the current API actor when one exists."""
    actor_type = AuditActorType.SYSTEM
    actor_id = None
    request_id = None
    if has_request_context():
        user = getattr(g, "current_user", None)
        if user is not None:
            actor_type = AuditActorType.USER
            actor_id = user.id
        try:
            request_id = uuid.UUID(getattr(g, "request_id", ""))
        except ValueError:
            request_id = None
    return record_event(session, actor_type=actor_type, actor_id=actor_id, request_id=request_id, **kwargs)


class AuditDatabaseError(RuntimeError):
    """Raised when audit persistence or retrieval fails."""


def record_event(
    session: Session,
    *,
    event_type: AuditEventType,
    entity_type: AuditEntityType,
    entity_id: uuid.UUID | None = None,
    actor_type: AuditActorType = AuditActorType.SYSTEM,
    actor_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    worker_id: uuid.UUID | None = None,
    workflow_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    job_attempt_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type, entity_type=entity_type, entity_id=entity_id,
        actor_type=actor_type, actor_id=actor_id, request_id=request_id,
        worker_id=worker_id, workflow_id=workflow_id, job_id=job_id,
        job_attempt_id=job_attempt_id, details=details or {},
    )
    session.add(event)
    return event


def _serialize(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": str(event.id), "event_type": event.event_type.value,
        "entity_type": event.entity_type.value, "entity_id": str(event.entity_id) if event.entity_id else None,
        "actor_type": event.actor_type.value, "actor_id": str(event.actor_id) if event.actor_id else None,
        "request_id": str(event.request_id) if event.request_id else None,
        "worker_id": str(event.worker_id) if event.worker_id else None,
        "workflow_id": str(event.workflow_id) if event.workflow_id else None,
        "job_id": str(event.job_id) if event.job_id else None,
        "job_attempt_id": str(event.job_attempt_id) if event.job_attempt_id else None,
        "details": event.details, "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def list_events(*, page: int, per_page: int, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    try:
        with session_scope() as session:
            conditions = []
            for field in ("event_type", "entity_type", "entity_id", "job_id", "workflow_id", "actor_id", "worker_id"):
                value = filters.get(field)
                if value is not None:
                    conditions.append(getattr(AuditEvent, field) == value)
            if filters.get("created_after") is not None:
                conditions.append(AuditEvent.created_at >= filters["created_after"])
            if filters.get("created_before") is not None:
                conditions.append(AuditEvent.created_at <= filters["created_before"])
            total = session.scalar(select(func.count()).select_from(AuditEvent).where(*conditions)) or 0
            events = list(session.scalars(select(AuditEvent).where(*conditions).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).offset((page - 1) * per_page).limit(per_page)))
            return [_serialize(event) for event in events], total
    except SQLAlchemyError as exc:
        raise AuditDatabaseError from exc


def get_event(event_id: uuid.UUID) -> dict[str, Any]:
    try:
        with session_scope() as session:
            event = session.get(AuditEvent, event_id)
            if event is None:
                raise KeyError
            return _serialize(event)
    except KeyError:
        raise
    except SQLAlchemyError as exc:
        raise AuditDatabaseError from exc