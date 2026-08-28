"""Durable records for permanently failed jobs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.job_attempt import JobAttempt
    from app.models.recurring_job import RecurringJob


class DeadLetterJob(Base):
    """A management record for a Job that permanently failed."""

    __tablename__ = "dead_letter_jobs"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_dead_letter_jobs_job_id"),
        Index("ix_dead_letter_jobs_failed_at", "failed_at"),
        Index("ix_dead_letter_jobs_task_type", "task_type"),
        Index("ix_dead_letter_jobs_recurring_job_id", "recurring_job_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    task_type: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_type: Mapped[str] = mapped_column(String(255), nullable=False)
    error_message: Mapped[str] = mapped_column(String(4000), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_attempts.id", ondelete="SET NULL"), nullable=True
    )
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="TASK_EXECUTION")
    recurring_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recurring_jobs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    job: Mapped["Job"] = relationship(back_populates="dead_letter")
    last_attempt: Mapped["JobAttempt | None"] = relationship()
    recurring_job: Mapped["RecurringJob | None"] = relationship()
