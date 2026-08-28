"""Safe API representations for ORM objects."""

from __future__ import annotations

from typing import Any

from app.models import Job
from app.models import DeadLetterJob
from app.models import Worker


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


def dead_letter_to_dict(record: DeadLetterJob) -> dict[str, Any]:
    """Convert a DLQ record to a JSON-safe public representation."""
    return {
        "id": str(record.id),
        "job_id": str(record.job_id),
        "task_type": record.task_type,
        "payload": record.payload,
        "error_type": record.error_type,
        "error_message": record.error_message,
        "attempt_count": record.attempt_count,
        "last_attempt_id": str(record.last_attempt_id) if record.last_attempt_id else None,
        "failed_at": _isoformat(record.failed_at),
        "source": record.source,
        "recurring_job_id": str(record.recurring_job_id) if record.recurring_job_id else None,
        "created_at": _isoformat(record.created_at),
        "updated_at": _isoformat(record.updated_at),
    }


def worker_to_dict(worker: Worker) -> dict[str, Any]:
    """Convert worker health metadata to a JSON-safe representation."""
    return {
        "worker_id": str(worker.id),
        "worker_name": worker.worker_name,
        "hostname": worker.hostname,
        "process_id": worker.process_id,
        "status": worker.status.value,
        "started_at": _isoformat(worker.started_at),
        "last_heartbeat_at": _isoformat(worker.last_heartbeat_at),
        "stopped_at": _isoformat(worker.stopped_at),
        "current_job_id": str(worker.current_job_id) if worker.current_job_id else None,
    }
