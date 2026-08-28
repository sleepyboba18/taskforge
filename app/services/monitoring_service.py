"""PostgreSQL-derived operational monitoring snapshots."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased

from app.database.session import get_engine, session_scope
from app.models import (
    AttemptStatus,
    AuditEvent,
    DeadLetterJob,
    Job,
    JobAttempt,
    JobDependency,
    JobStatus,
    Worker,
    WorkerStatus,
    Workflow,
    WorkflowStatus,
)

WINDOWS = {
    "1m": timedelta(minutes=1), "5m": timedelta(minutes=5), "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1), "6h": timedelta(hours=6), "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}
_PROCESS_STARTED = time.monotonic()


class MonitoringDatabaseError(RuntimeError):
    """Raised when an operational snapshot cannot be read."""


def validate_window(value: str | None) -> str:
    window = value or "1h"
    if window not in WINDOWS:
        raise ValueError("window must be one of 1m, 5m, 15m, 1h, 6h, 24h, or 7d")
    return window


def get_monitoring_snapshot(*, window: str, settings: Any) -> dict[str, Any]:
    window = validate_window(window)
    now = datetime.now(timezone.utc)
    cutoff = now - WINDOWS[window]
    previous_cutoff = cutoff - WINDOWS[window]
    started = time.perf_counter()
    try:
        with session_scope() as session:
            status_counts = dict(session.execute(select(Job.status, func.count()).group_by(Job.status)).all())
            workflow_counts = dict(session.execute(select(Workflow.status, func.count()).group_by(Workflow.status)).all())
            worker_counts = dict(session.execute(select(Worker.status, func.count()).group_by(Worker.status)).all())
            active_workers = worker_counts.get(WorkerStatus.IDLE, 0) + worker_counts.get(WorkerStatus.BUSY, 0)
            busy_workers = worker_counts.get(WorkerStatus.BUSY, 0)
            pending = status_counts.get(JobStatus.PENDING, 0)
            scheduled = status_counts.get(JobStatus.SCHEDULED, 0)
            dependency_parent = aliased(Job)
            blocked = session.scalar(select(func.count()).select_from(Job).where(
                Job.status.in_([JobStatus.PENDING, JobStatus.SCHEDULED, JobStatus.RETRYING]),
                exists(select(JobDependency.id).where(JobDependency.job_id == Job.id)),
                exists(select(JobDependency.id).join(dependency_parent, dependency_parent.id == JobDependency.depends_on_job_id).where(
                    JobDependency.job_id == Job.id, dependency_parent.status != JobStatus.COMPLETED
                )),
            )) or 0
            completed_window = session.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.COMPLETED, Job.updated_at >= cutoff)) or 0
            failed_window = session.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.FAILED, Job.updated_at >= cutoff)) or 0
            cancelled_window = session.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.CANCELLED, Job.updated_at >= cutoff)) or 0
            retry_window = session.scalar(select(func.count()).select_from(Job).where(Job.retry_count > 0, Job.updated_at >= cutoff)) or 0
            previous_failed = session.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.FAILED, Job.updated_at >= previous_cutoff, Job.updated_at < cutoff)) or 0
            previous_retry = session.scalar(select(func.count()).select_from(Job).where(Job.retry_count > 0, Job.updated_at >= previous_cutoff, Job.updated_at < cutoff)) or 0
            attempt_duration = func.extract("epoch", JobAttempt.finished_at - JobAttempt.started_at) * 1000
            latency = session.scalar(select(func.avg(attempt_duration)).where(JobAttempt.status == AttemptStatus.COMPLETED, JobAttempt.finished_at.is_not(None), JobAttempt.started_at >= cutoff)) or 0
            oldest_pending = session.scalar(select(func.min(Job.created_at)).where(Job.status == JobStatus.PENDING))
            long_running = session.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.RUNNING, Job.started_at.is_not(None), Job.started_at < now - timedelta(seconds=settings.long_running_job_threshold_seconds))) or 0
            dlq_depth = session.scalar(select(func.count()).select_from(DeadLetterJob)) or 0
            dlq_window = session.scalar(select(func.count()).select_from(DeadLetterJob).where(DeadLetterJob.failed_at >= cutoff)) or 0
            audit_total = session.scalar(select(func.count()).select_from(AuditEvent)) or 0
            dependency_edges = session.scalar(select(func.count()).select_from(JobDependency)) or 0
            database_latency_ms = (time.perf_counter() - started) * 1000
            queue_depth = pending
            utilization = round(busy_workers / active_workers * 100, 2) if active_workers else 0
            alerts = _alerts(
                queue_depth=queue_depth, pending=pending, active_workers=active_workers,
                stale_workers=worker_counts.get(WorkerStatus.STALE, 0), utilization=utilization,
                dlq_depth=dlq_depth, failed_window=failed_window, previous_failed=previous_failed,
                retry_window=retry_window, previous_retry=previous_retry, settings=settings,
            )
            return {
                "status": "HEALTHY" if not any(alert["severity"] == "CRITICAL" for alert in alerts) else "DEGRADED",
                "window": window,
                "system": {"uptime_seconds": round(time.monotonic() - _PROCESS_STARTED, 2), "current_time": now.isoformat(), "environment": settings.app_env},
                "queue": {"pending_jobs": pending, "running_jobs": status_counts.get(JobStatus.RUNNING, 0), "scheduled_jobs": scheduled, "retry_waiting_jobs": status_counts.get(JobStatus.RETRYING, 0), "completed_jobs": status_counts.get(JobStatus.COMPLETED, 0), "failed_jobs": status_counts.get(JobStatus.FAILED, 0), "cancelled_jobs": status_counts.get(JobStatus.CANCELLED, 0), "dependency_blocked_jobs": blocked, "queue_depth": queue_depth, "oldest_pending_job_age_seconds": max(0, int((now - oldest_pending).total_seconds())) if oldest_pending else 0},
                "workers": {"total_workers": sum(worker_counts.values()), "active_workers": active_workers, "idle_workers": worker_counts.get(WorkerStatus.IDLE, 0), "busy_workers": busy_workers, "stale_workers": worker_counts.get(WorkerStatus.STALE, 0), "offline_workers": worker_counts.get(WorkerStatus.STOPPED, 0), "healthy_workers": active_workers - worker_counts.get(WorkerStatus.STALE, 0), "utilization_percent": utilization, "worker_capacity": active_workers, "available_capacity": max(0, active_workers - busy_workers)},
                "jobs": {"jobs_completed_total": status_counts.get(JobStatus.COMPLETED, 0), "jobs_failed_total": status_counts.get(JobStatus.FAILED, 0), "jobs_cancelled_total": status_counts.get(JobStatus.CANCELLED, 0), "jobs_retried_total": retry_window, "completion_rate_per_hour": round(completed_window / max(WINDOWS[window].total_seconds() / 3600, 1 / 60), 2), "failure_rate": _rate(failed_window, completed_window + failed_window), "retry_rate": _rate(retry_window, max(completed_window + failed_window, 1)), "success_rate": _rate(completed_window, completed_window + failed_window), "average_execution_time_ms": round(float(latency), 2), "average_queue_wait_ms": 0, "average_total_job_time_ms": 0, "long_running_jobs": long_running},
                "workflows": {"active_workflows": workflow_counts.get(WorkflowStatus.PENDING, 0) + workflow_counts.get(WorkflowStatus.RUNNING, 0), "completed_workflows": workflow_counts.get(WorkflowStatus.SUCCEEDED, 0), "failed_workflows": workflow_counts.get(WorkflowStatus.FAILED, 0), "cancelled_workflows": workflow_counts.get(WorkflowStatus.CANCELLED, 0), "blocked_workflows": 0},
                "scheduler": {"scheduler_running": True, "scheduled_jobs": scheduled, "recurring_jobs": 0, "next_scheduled_job": None, "missed_schedule_count": 0},
                "dlq": {"dlq_depth": dlq_depth, "dlq_entries_in_window": dlq_window, "dlq_retries_in_window": 0, "dlq_discards_in_window": 0},
                "dependencies": {"total_dependencies": dependency_edges, "blocked_jobs": blocked, "failed_dependency_chains": 0},
                "database": {"database_status": "healthy", "database_latency_ms": round(database_latency_ms, 2), **_pool_metrics()},
                "api": {"request_count": 0, "error_count": 0, "average_latency_ms": 0, "slow_requests": 0},
                "audit": {"audit_events_total": audit_total},
                "alerts": alerts,
            }
    except (SQLAlchemyError, KeyError) as exc:
        raise MonitoringDatabaseError from exc


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def _pool_metrics() -> dict[str, int | None]:
    try:
        pool = get_engine().pool
        return {"pool_size": pool.size(), "checked_out_connections": pool.checkedout(), "overflow": pool.overflow()}
    except (AttributeError, RuntimeError):
        return {"pool_size": None, "checked_out_connections": None, "overflow": None}


def _alerts(*, queue_depth: int, pending: int, active_workers: int, stale_workers: int, utilization: float, dlq_depth: int, failed_window: int, previous_failed: int, retry_window: int, previous_retry: int, settings: Any) -> list[dict[str, str]]:
    alerts = []
    if queue_depth >= settings.queue_backlog_warning_threshold:
        alerts.append({"code": "QUEUE_BACKLOG_HIGH", "severity": "WARNING", "message": "Queue backlog exceeds configured threshold."})
    if pending and active_workers == 0:
        alerts.append({"code": "QUEUE_STARVATION", "severity": "CRITICAL", "message": "Pending jobs exist but no active workers are available."})
    if stale_workers:
        alerts.append({"code": "STALE_WORKERS_PRESENT", "severity": "WARNING", "message": "One or more workers have stale heartbeats."})
    if active_workers == 0:
        alerts.append({"code": "NO_ACTIVE_WORKERS", "severity": "WARNING", "message": "No active workers are registered."})
    if utilization >= settings.worker_saturation_threshold_percent:
        alerts.append({"code": "WORKER_SATURATION", "severity": "WARNING", "message": "Worker utilization exceeds configured threshold."})
    if dlq_depth >= settings.dlq_backlog_warning_threshold:
        alerts.append({"code": "DLQ_BACKLOG_HIGH", "severity": "WARNING", "message": "Dead-letter queue exceeds configured threshold."})
    if previous_failed and failed_window > previous_failed * 1.5:
        alerts.append({"code": "FAILURE_SPIKE", "severity": "WARNING", "message": "Failure volume increased significantly."})
    if previous_retry and retry_window > previous_retry * 1.5:
        alerts.append({"code": "RETRY_SPIKE", "severity": "WARNING", "message": "Retry volume increased significantly."})
    return alerts
