"""Database-backed health and metrics queries."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, exists, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased

from app.database.session import check_database_connection, session_scope
from app.models import AttemptStatus, AuditEvent, AuditEventType, DeadLetterJob, Job, JobAttempt, JobDependency, JobStatus, Worker, WorkerStatus, Workflow, WorkflowStatus

logger = logging.getLogger("taskforge.observability")


class ObservabilityDatabaseError(RuntimeError):
    """Raised when health or metrics data cannot be read."""


def database_is_ready() -> bool:
    """Return whether the configured database accepts a lightweight query."""
    try:
        check_database_connection()
        return True
    except SQLAlchemyError:
        logger.exception("Database readiness check failed")
        return False


def collect_metrics(*, window: str) -> dict[str, Any]:
    """Collect bounded database aggregates for the requested UTC window."""
    duration = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}.get(window)
    if duration is None:
        raise ValueError("window must be 1h, 24h, or 7d")
    cutoff = datetime.now(timezone.utc) - duration
    try:
        with session_scope() as session:
            status_counts = dict(
                session.execute(
                    select(Job.status, func.count()).group_by(Job.status)
                ).all()
            )
            completed, failed, retrying = (
                status_counts.get(JobStatus.COMPLETED, 0),
                status_counts.get(JobStatus.FAILED, 0),
                status_counts.get(JobStatus.RETRYING, 0),
            )
            queued = status_counts.get(JobStatus.PENDING, 0) + status_counts.get(JobStatus.SCHEDULED, 0)
            dead_lettered = session.scalar(select(func.count()).select_from(DeadLetterJob)) or 0
            throughput = session.execute(
                select(
                    func.count().filter(Job.status == JobStatus.COMPLETED),
                    func.count().filter(Job.status == JobStatus.FAILED),
                ).where(Job.updated_at >= cutoff)
            ).one()
            completed_window, failed_window = throughput[0], throughput[1]
            attempt_duration = func.extract(
                "epoch", JobAttempt.finished_at - JobAttempt.started_at
            ) * 1000
            latency = session.execute(
                select(func.avg(attempt_duration), func.min(attempt_duration), func.max(attempt_duration))
                .where(
                    JobAttempt.status == AttemptStatus.COMPLETED,
                    JobAttempt.finished_at.is_not(None),
                    JobAttempt.started_at >= cutoff,
                )
            ).one()
            total_outcomes = completed_window + failed_window
            healthy_workers = session.scalar(
                select(func.count()).select_from(Worker).where(
                    Worker.status.in_([WorkerStatus.IDLE, WorkerStatus.BUSY]),
                    Worker.last_heartbeat_at.is_not(None),
                    Worker.last_heartbeat_at >= cutoff,
                )
            ) or 0
            stale_workers = session.scalar(
                select(func.count()).select_from(Worker).where(Worker.status == WorkerStatus.STALE)
            ) or 0
            total_workers = session.scalar(select(func.count()).select_from(Worker)) or 0
            workflow_counts = dict(session.execute(select(Workflow.status, func.count()).group_by(Workflow.status)).all())
            audit_events_total = session.scalar(select(func.count()).select_from(AuditEvent)) or 0
            admin_actions_total = session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type.in_([AuditEventType.QUEUE_PAUSED, AuditEventType.QUEUE_RESUMED, AuditEventType.BULK_JOB_CANCEL, AuditEventType.BULK_JOB_RETRY, AuditEventType.ADMIN_ACTION]))) or 0
            dependency_edges = session.scalar(select(func.count()).select_from(JobDependency)) or 0
            dependency_parent = aliased(Job)
            dependency_waiting = session.scalar(
                select(func.count()).select_from(Job).where(
                    Job.status.in_([JobStatus.PENDING, JobStatus.SCHEDULED, JobStatus.RETRYING]),
                    exists(select(JobDependency.id).where(JobDependency.job_id == Job.id)),
                    exists(
                        select(JobDependency.id).join(dependency_parent, dependency_parent.id == JobDependency.depends_on_job_id).where(
                            JobDependency.job_id == Job.id, dependency_parent.status != JobStatus.COMPLETED
                        )
                    ),
                )
            ) or 0
            dependency_blocked = session.scalar(
                select(func.count()).select_from(Job).where(
                    Job.status == JobStatus.CANCELLED,
                    Job.last_error.like("Dependency job %"),
                )
            ) or 0
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "window": window,
                "jobs": {
                    "queued": queued,
                    "running": status_counts.get(JobStatus.RUNNING, 0),
                    "succeeded": completed,
                    "failed": failed,
                    "retrying": retrying,
                    "dead_lettered": dead_lettered,
                    "cancelled": status_counts.get(JobStatus.CANCELLED, 0),
                },
                "throughput": {
                    "completed_jobs": completed_window,
                    "failed_jobs": failed_window,
                    "retried_jobs": session.scalar(
                        select(func.count()).select_from(Job).where(Job.retry_count > 0, Job.updated_at >= cutoff)
                    ) or 0,
                    "dead_lettered_jobs": session.scalar(
                        select(func.count()).select_from(DeadLetterJob).where(DeadLetterJob.failed_at >= cutoff)
                    ) or 0,
                },
                "performance": {
                    "average_execution_time_ms": float(latency[0]) if latency[0] is not None else 0.0,
                    "minimum_execution_time_ms": float(latency[1]) if latency[1] is not None else 0.0,
                    "maximum_execution_time_ms": float(latency[2]) if latency[2] is not None else 0.0,
                    "success_rate": completed_window / total_outcomes if total_outcomes else 0.0,
                    "failure_rate": failed_window / total_outcomes if total_outcomes else 0.0,
                },
                "workers": {
                    "total": total_workers,
                    "healthy": healthy_workers,
                    "stale": stale_workers,
                    "active": status_counts.get(JobStatus.RUNNING, 0),
                },
                "dependencies": {
                    "waiting_jobs": dependency_waiting,
                    "blocked_jobs": dependency_blocked,
                    "dependency_edges": dependency_edges,
                },
                "workflows": {
                    "total": sum(workflow_counts.values()),
                    "pending": workflow_counts.get(WorkflowStatus.PENDING, 0),
                    "running": workflow_counts.get(WorkflowStatus.RUNNING, 0),
                    "succeeded": workflow_counts.get(WorkflowStatus.SUCCEEDED, 0),
                    "failed": workflow_counts.get(WorkflowStatus.FAILED, 0),
                    "cancelled": workflow_counts.get(WorkflowStatus.CANCELLED, 0),
                },
                "audit": {"events_total": audit_events_total, "admin_actions_total": admin_actions_total},
            }
    except (SQLAlchemyError, KeyError) as exc:
        logger.exception("Metrics aggregation failed")
        raise ObservabilityDatabaseError from exc


def detailed_health(*, stale_timeout: float) -> dict[str, Any]:
    """Return safe aggregate health state without infrastructure secrets."""
    try:
        metrics = collect_metrics(window="24h")
        database = database_is_ready()
        workers = metrics["workers"]
        return {
            "status": "healthy" if database else "degraded",
            "database": "healthy" if database else "unhealthy",
            "workers": "healthy" if workers["healthy"] else "unhealthy",
            "scheduler": "unknown",
            "queue": "healthy" if database else "unknown",
        }
    except (ObservabilityDatabaseError, SQLAlchemyError):
        return {"status": "degraded", "database": "unhealthy", "workers": "unknown", "scheduler": "unknown", "queue": "unknown"}
