"""Append-only business and security history."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


class AuditEventType(str, Enum):
    JOB_CREATED = "JOB_CREATED"
    JOB_STATE_CHANGED = "JOB_STATE_CHANGED"
    JOB_RETRIED = "JOB_RETRIED"
    JOB_CANCELLED = "JOB_CANCELLED"
    JOB_COMPLETED = "JOB_COMPLETED"
    JOB_FAILED = "JOB_FAILED"
    JOB_ATTEMPT_STARTED = "JOB_ATTEMPT_STARTED"
    JOB_ATTEMPT_SUCCEEDED = "JOB_ATTEMPT_SUCCEEDED"
    JOB_ATTEMPT_FAILED = "JOB_ATTEMPT_FAILED"
    WORKFLOW_CREATED = "WORKFLOW_CREATED"
    WORKFLOW_STATE_CHANGED = "WORKFLOW_STATE_CHANGED"
    WORKFLOW_RETRIED = "WORKFLOW_RETRIED"
    WORKFLOW_CANCELLED = "WORKFLOW_CANCELLED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    DEPENDENCY_CREATED = "DEPENDENCY_CREATED"
    DEPENDENCY_REMOVED = "DEPENDENCY_REMOVED"
    DLQ_ENTERED = "DLQ_ENTERED"
    DLQ_RETRIED = "DLQ_RETRIED"
    DLQ_DISCARDED = "DLQ_DISCARDED"
    WORKER_REGISTERED = "WORKER_REGISTERED"
    WORKER_MARKED_STALE = "WORKER_MARKED_STALE"
    BULK_JOB_CANCEL = "BULK_JOB_CANCEL"
    BULK_JOB_RETRY = "BULK_JOB_RETRY"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"


class AuditEntityType(str, Enum):
    JOB = "JOB"
    JOB_ATTEMPT = "JOB_ATTEMPT"
    WORKFLOW = "WORKFLOW"
    WORKER = "WORKER"
    DEPENDENCY = "DEPENDENCY"
    DLQ = "DLQ"
    SCHEDULE = "SCHEDULE"
    SYSTEM = "SYSTEM"


class AuditActorType(str, Enum):
    USER = "USER"
    WORKER = "WORKER"
    SYSTEM = "SYSTEM"


class AuditEvent(Base):
    """Immutable audit event; the application exposes insertion and reads only."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id", "created_at"),
        Index("ix_audit_events_job", "job_id", "created_at"),
        Index("ix_audit_events_workflow", "workflow_id", "created_at"),
        Index("ix_audit_events_actor", "actor_id", "created_at"),
        Index("ix_audit_events_worker", "worker_id", "created_at"),
        Index("ix_audit_events_created_at", "created_at"),
        Index("ix_audit_events_event_type", "event_type", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[AuditEventType] = mapped_column(SqlEnum(AuditEventType, name="audit_event_type"), nullable=False)
    entity_type: Mapped[AuditEntityType] = mapped_column(SqlEnum(AuditEntityType, name="audit_entity_type"), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_type: Mapped[AuditActorType] = mapped_column(SqlEnum(AuditActorType, name="audit_actor_type"), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    worker_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("workers.id", ondelete="SET NULL"), nullable=True)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    job_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("job_attempts.id", ondelete="SET NULL"), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
