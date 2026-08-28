"""Persisted recurring job definitions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base

if TYPE_CHECKING:
    from app.models.job import Job


class RecurringJob(Base):
    """A durable five-field cron definition that generates normal Jobs."""

    __tablename__ = "recurring_jobs"
    __table_args__ = (
        CheckConstraint("priority >= 0", name="ck_recurring_jobs_priority_non_negative"),
        CheckConstraint("max_retries >= 0", name="ck_recurring_jobs_max_retries_non_negative"),
        Index("ix_recurring_jobs_due", "enabled", "next_run_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    schedule_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    jobs: Mapped[list["Job"]] = relationship(back_populates="recurring_job")
