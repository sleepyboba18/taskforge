"""Persistent background job ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, DateTime, Enum as SqlEnum, ForeignKey, Index, Integer, String, desc, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.enums import JobStatus

if TYPE_CHECKING:
    from app.models.job_attempt import JobAttempt
    from app.models.worker import Worker


class Job(Base):
    """A durable unit of work persisted in PostgreSQL."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("priority >= 0", name="ck_jobs_priority_non_negative"),
        CheckConstraint("max_retries >= 0", name="ck_jobs_max_retries_non_negative"),
        CheckConstraint("retry_count >= 0", name="ck_jobs_retry_count_non_negative"),
        Index(
            "ix_jobs_queue_claim",
            "status",
            "scheduled_at",
            desc("priority"),
            "created_at",
        ),
        Index("ix_jobs_status_priority_created", "status", desc("priority"), "created_at"),
        Index("ix_jobs_retry_promotion", "status", "next_retry_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[JobStatus] = mapped_column(
        SqlEnum(JobStatus, name="job_status"), nullable=False, default=JobStatus.PENDING, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    worker_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workers.id", ondelete="SET NULL"), nullable=True, index=True
    )

    worker: Mapped["Worker | None"] = relationship(back_populates="jobs")
    attempts: Mapped[list["JobAttempt"]] = relationship(back_populates="job")
