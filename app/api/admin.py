"""Authorized operational control endpoints."""

from __future__ import annotations

import uuid

from flask import Blueprint, current_app, jsonify, request

from app.auth.decorators import require_roles
from app.auth.permissions import OPERATORS
from app.models import UserRole
from app.rate_limit import rate_limit
from app.services.admin_service import AdminControlDatabaseError, queue_status, set_queue_paused
from app.services.dead_letter_service import (
    DeadLetterConflictError,
    DeadLetterDatabaseError,
    DeadLetterNotFoundError,
    requeue_job_by_id,
)
from app.services.job_service import (
    JobDatabaseError,
    JobNotFoundError,
    JobStateConflictError,
    bulk_cancel_jobs,
    bulk_retry_jobs,
    cancel_job,
)
from app.services.monitoring_service import MonitoringDatabaseError, get_monitoring_snapshot

admin_bp = Blueprint("admin", __name__, url_prefix="/api/v1/admin")


def _error(code: str, message: str, status: int):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def _reason():
    body = request.get_json(silent=True)
    if body is None:
        return None
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object.")
    reason = body.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 500 or any(ord(char) < 32 and char not in "\t" for char in reason)):
        raise ValueError("reason must be at most 500 characters without control characters.")
    return reason.strip() if isinstance(reason, str) else None


@admin_bp.post("/queue/pause")
@require_roles(*OPERATORS)
@rate_limit("admin")
def pause_queue():
    try:
        result = set_queue_paused(paused=True)
    except AdminControlDatabaseError:
        return _error("DATABASE_ERROR", "Unable to pause queue.", 503)
    return jsonify({"success": True, "data": result})


@admin_bp.post("/queue/resume")
@require_roles(*OPERATORS)
@rate_limit("admin")
def resume_queue():
    try:
        result = set_queue_paused(paused=False)
    except AdminControlDatabaseError:
        return _error("DATABASE_ERROR", "Unable to resume queue.", 503)
    return jsonify({"success": True, "data": result})


@admin_bp.get("/queue/status")
@require_roles(*OPERATORS)
@rate_limit("read")
def queue_control_status():
    try:
        return jsonify({"success": True, "data": queue_status()})
    except AdminControlDatabaseError:
        return _error("DATABASE_ERROR", "Unable to read queue status.", 503)


@admin_bp.post("/jobs/<job_id>/cancel")
@require_roles(*OPERATORS)
@rate_limit("admin")
def admin_cancel_job(job_id: str):
    parsed = _parse_id(job_id)
    if parsed is None:
        return _error("JOB_NOT_FOUND", "Job not found.", 404)
    try:
        reason = _reason()
        job = cancel_job(parsed, reason=reason)
    except ValueError as exc:
        return _error("VALIDATION_ERROR", str(exc), 400)
    except JobNotFoundError:
        return _error("JOB_NOT_FOUND", "Job not found.", 404)
    except JobStateConflictError:
        return _error("JOB_NOT_CANCELLABLE", "Job is not cancellable in its current state.", 409)
    except JobDatabaseError:
        return _error("DATABASE_ERROR", "Unable to cancel job.", 503)
    return jsonify({"success": True, "data": {"job_id": str(job.id), "status": job.status.value}})


@admin_bp.post("/jobs/<job_id>/retry")
@require_roles(*OPERATORS)
@rate_limit("admin")
def admin_retry_job(job_id: str):
    parsed = _parse_id(job_id)
    if parsed is None:
        return _error("JOB_NOT_FOUND", "Job not found.", 404)
    try:
        results = bulk_retry_jobs([parsed])
    except JobDatabaseError:
        return _error("DATABASE_ERROR", "Unable to retry job.", 503)
    result = results[0]
    return jsonify({"success": result["status"] == "retrying", "data": result}), (200 if result["status"] == "retrying" else 409)


@admin_bp.post("/dlq/<job_id>/requeue")
@require_roles(*OPERATORS)
@rate_limit("admin")
def admin_requeue_dlq(job_id: str):
    parsed = _parse_id(job_id)
    if parsed is None:
        return _error("DLQ_ENTRY_NOT_FOUND", "DLQ entry not found.", 404)
    try:
        reason = _reason()
        job = requeue_job_by_id(parsed, reason=reason)
    except ValueError as exc:
        return _error("VALIDATION_ERROR", str(exc), 400)
    except DeadLetterNotFoundError:
        return _error("DLQ_ENTRY_NOT_FOUND", "DLQ entry not found.", 404)
    except DeadLetterConflictError:
        return _error("ACTION_CONFLICT", "DLQ entry cannot be requeued.", 409)
    except DeadLetterDatabaseError:
        return _error("DATABASE_ERROR", "Unable to requeue DLQ job.", 503)
    return jsonify({"success": True, "data": {"job_id": str(job.id), "status": job.status.value}})


@admin_bp.post("/jobs/cancel")
@require_roles(*OPERATORS)
@rate_limit("admin")
def admin_bulk_cancel():
    return _bulk(bulk_cancel_jobs, "cancel")


@admin_bp.post("/jobs/retry")
@require_roles(*OPERATORS)
@rate_limit("admin")
def admin_bulk_retry():
    return _bulk(bulk_retry_jobs, "retry")


@admin_bp.get("/status")
@require_roles(*OPERATORS)
@rate_limit("read")
def admin_status():
    try:
        settings = current_app.config["TASKFORGE_SETTINGS"]
        snapshot = get_monitoring_snapshot(window="1m", settings=settings)
        status = queue_status()
    except (AdminControlDatabaseError, MonitoringDatabaseError):
        return _error("MONITORING_UNAVAILABLE", "Administrative status is temporarily unavailable.", 503)
    return jsonify({"success": True, "data": {"queue_paused": status["paused"], "maintenance_mode": False, "scheduler_status": snapshot["scheduler"], "worker_summary": snapshot["workers"], "active_alert_count": len(snapshot["alerts"])}})


def _bulk(action, operation):
    body = request.get_json(silent=True)
    ids = body.get("job_ids") if isinstance(body, dict) else None
    maximum = current_app.config["TASKFORGE_SETTINGS"].admin_bulk_action_limit
    if not isinstance(ids, list) or len(ids) > maximum or len(set(ids)) != len(ids):
        return _error("VALIDATION_ERROR", f"job_ids must be a unique array of at most {maximum} IDs.", 400)
    try:
        parsed = [uuid.UUID(value) for value in ids]
    except (ValueError, TypeError):
        return _error("VALIDATION_ERROR", "job_ids must contain valid UUID strings.", 400)
    try:
        results = action(parsed, max_dependency_propagation_depth=current_app.config["TASKFORGE_SETTINGS"].max_dependency_propagation_depth) if operation == "cancel" else action(parsed)
    except JobDatabaseError:
        return _error("DATABASE_ERROR", f"Unable to bulk {operation} jobs.", 503)
    return jsonify({"success": True, "data": {"requested": len(results), "succeeded": sum(item["status"] in {"cancelled", "retrying"} for item in results), "failed": sum(item["status"] not in {"cancelled", "retrying"} for item in results), "results": results}})


def _parse_id(value: str):
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
