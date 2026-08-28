"""Persistent directed edges between jobs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base

if TYPE_CHECKING:
    from app.models.job import Job


class JobDependency(Base):
    """An edge where job_id depends on depends_on_job_id."""

    __tablename__ = "job_dependencies"
    __table_args__ = (
        UniqueConstraint("job_id", "depends_on_job_id", name="uq_job_dependency_edge"),
        Index("ix_job_dependencies_job_id", "job_id"),
        Index("ix_job_dependencies_depends_on_job_id", "depends_on_job_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    depends_on_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    job: Mapped["Job"] = relationship(foreign_keys=[job_id], back_populates="dependencies")
    depends_on: Mapped["Job"] = relationship(
        foreign_keys=[depends_on_job_id], back_populates="dependents"
    )