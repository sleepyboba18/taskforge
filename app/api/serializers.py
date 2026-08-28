"""Safe API representations for ORM objects."""

from __future__ import annotations

from typing import Any

from app.models import Job


def job_to_dict(job: Job) -> dict[str, Any]:
    """Convert a job to a JSON-safe public representation."""
    return {
        "id": str(job.id),
        "name": job.name,
        "task_type": job.task_type,
        "payload": job.payload,
        "status": job.status.value,
        "priority": job.priority,
        "max_retries": job.max_retries,
        "retry_count": job.retry_count,
        "scheduled_at": _isoformat(job.scheduled_at),
        "next_retry_at": _isoformat(job.next_retry_at),
        "created_at": _isoformat(job.created_at),
        "updated_at": _isoformat(job.updated_at),
        "started_at": _isoformat(job.started_at),
        "completed_at": _isoformat(job.completed_at),
        "last_error": job.last_error,
        "worker_id": str(job.worker_id) if job.worker_id else None,
        "recurring_job_id": str(job.recurring_job_id) if job.recurring_job_id else None,
    }


def _isoformat(value: Any) -> str | None:
    return value.isoformat() if value is not None else None
