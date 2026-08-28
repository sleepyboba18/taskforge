"""REST endpoints for recurring cron job definitions."""

from __future__ import annotations

import uuid
from typing import Any

from flask import Blueprint, jsonify, request

from app.services.recurring_job_service import (
    RecurringJobDatabaseError,
    RecurringJobNotFoundError,
    create_recurring_job,
    get_recurring_job,
    list_recurring_jobs,
    set_recurring_enabled,
)
from app.services.recurring_schedule_service import ScheduleValidationError
from app.auth.decorators import require_roles
from app.auth.permissions import AUTHENTICATED, OPERATORS
from app.rate_limit import rate_limit

recurring_jobs_bp = Blueprint("recurring_jobs", __name__, url_prefix="/api/v1/recurring-jobs")


@recurring_jobs_bp.post("")
@require_roles(*OPERATORS)
@rate_limit("write")
def create_recurring_job_endpoint():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error("VALIDATION_ERROR", "Request body must be a JSON object.", 400)
    values, errors = _validate_input(body)
    if errors:
        return _error("VALIDATION_ERROR", "Invalid request data.", 400, errors)
    try:
        recurring = create_recurring_job(**values)
    except ScheduleValidationError as exc:
        return _error("VALIDATION_ERROR", str(exc), 400)
    except RecurringJobDatabaseError:
        return _error("DATABASE_ERROR", "Unable to persist recurring job.", 500)
    return jsonify({"success": True, "data": recurring_to_dict(recurring)}), 201


@recurring_jobs_bp.get("")
@require_roles(*AUTHENTICATED)
@rate_limit("read")
def list_recurring_job_endpoint():
    enabled_value = request.args.get("enabled")
    enabled = None
    if enabled_value is not None:
        if enabled_value.lower() not in {"true", "false"}:
            return _error("VALIDATION_ERROR", "enabled must be true or false.", 400)
        enabled = enabled_value.lower() == "true"
    try:
        recurring_jobs = list_recurring_jobs(enabled=enabled)
    except RecurringJobDatabaseError:
        return _error("DATABASE_ERROR", "Unable to list recurring jobs.", 500)
    return jsonify({"success": True, "data": [recurring_to_dict(job) for job in recurring_jobs]})


@recurring_jobs_bp.get("/<recurring_job_id>")
@require_roles(*AUTHENTICATED)
@rate_limit("read")
def get_recurring_job_endpoint(recurring_job_id: str):
    parsed_id, error = _parse_uuid(recurring_job_id)
    if error:
        return error
    try:
        recurring = get_recurring_job(parsed_id)
    except RecurringJobNotFoundError:
        return _error("RECURRING_JOB_NOT_FOUND", "Recurring job not found.", 404)
    except RecurringJobDatabaseError:
        return _error("DATABASE_ERROR", "Unable to retrieve recurring job.", 500)
    return jsonify({"success": True, "data": recurring_to_dict(recurring)})


@recurring_jobs_bp.post("/<recurring_job_id>/disable")
@require_roles(*OPERATORS)
@rate_limit("write")
def disable_recurring_job_endpoint(recurring_job_id: str):
    return _set_enabled(recurring_job_id, False)


@recurring_jobs_bp.post("/<recurring_job_id>/enable")
@require_roles(*OPERATORS)
@rate_limit("write")
def enable_recurring_job_endpoint(recurring_job_id: str):
    return _set_enabled(recurring_job_id, True)


def _set_enabled(recurring_job_id: str, enabled: bool):
    parsed_id, error = _parse_uuid(recurring_job_id)
    if error:
        return error
    try:
        recurring = set_recurring_enabled(parsed_id, enabled)
    except RecurringJobNotFoundError:
        return _error("RECURRING_JOB_NOT_FOUND", "Recurring job not found.", 404)
    except (RecurringJobDatabaseError, ScheduleValidationError):
        return _error("DATABASE_ERROR", "Unable to update recurring job.", 500)
    return jsonify({"success": True, "data": recurring_to_dict(recurring)})


def _validate_input(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    errors: dict[str, str] = {}
    name = body.get("name")
    task_type = body.get("task_type")
    payload = body.get("payload", {})
    schedule = body.get("schedule")
    timezone_name = body.get("timezone", "UTC")
    priority = body.get("priority", 5)
    max_retries = body.get("max_retries", 3)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
        errors["name"] = "Name must be a non-empty string of at most 255 characters."
    if not isinstance(task_type, str) or not task_type.strip() or len(task_type.strip()) > 255:
        errors["task_type"] = "Task type must be a non-empty string of at most 255 characters."
    if not isinstance(payload, dict):
        errors["payload"] = "Payload must be a JSON object."
    if not isinstance(schedule, str) or not schedule.strip():
        errors["schedule"] = "Schedule must be a five-field cron expression."
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        errors["timezone"] = "Timezone must be a valid IANA timezone."
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0:
        errors["priority"] = "Priority must be a non-negative integer."
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
        errors["max_retries"] = "Max retries must be a non-negative integer."
    if errors:
        return {}, errors
    return {
        "name": name.strip(), "task_type": task_type.strip(), "payload": payload,
        "priority": priority, "max_retries": max_retries,
        "schedule": schedule.strip(), "timezone_name": timezone_name.strip(),
    }, {}


def recurring_to_dict(recurring) -> dict[str, Any]:
    return {
        "id": str(recurring.id), "name": recurring.name, "task_type": recurring.task_type,
        "payload": recurring.payload, "priority": recurring.priority,
        "max_retries": recurring.max_retries, "schedule": recurring.schedule_expression,
        "timezone": recurring.timezone, "enabled": recurring.enabled,
        "next_run_at": recurring.next_run_at.isoformat(),
        "last_run_at": recurring.last_run_at.isoformat() if recurring.last_run_at else None,
        "created_at": recurring.created_at.isoformat() if recurring.created_at else None,
        "updated_at": recurring.updated_at.isoformat() if recurring.updated_at else None,
    }


def _parse_uuid(value: str):
    try:
        return uuid.UUID(value), None
    except (ValueError, AttributeError):
        return None, _error("VALIDATION_ERROR", "Recurring job ID must be a valid UUID.", 400)


def _error(code: str, message: str, status: int, details: dict[str, str] | None = None):
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return jsonify({"success": False, "error": error}), status
