"""REST endpoints for durable job submission and state inspection."""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, jsonify, request

from app.api.serializers import job_to_dict
from app.models import JobStatus
from app.services.job_service import (
    JobDatabaseError,
    JobNotFoundError,
    JobStateConflictError,
    cancel_job,
    create_job,
    get_job_by_id,
    list_jobs,
)

jobs_bp = Blueprint("jobs", __name__, url_prefix="/api/v1/jobs")
MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 20


@jobs_bp.post("")
def submit_job():
    """Validate and persist a new job."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error("VALIDATION_ERROR", "Request body must be a JSON object.", status=400)

    values, errors = _validate_job_input(body)
    if errors:
        return _error("VALIDATION_ERROR", "Invalid request data.", details=errors, status=400)

    try:
        job = create_job(**values)
    except JobDatabaseError:
        return _error("DATABASE_ERROR", "Unable to persist job.", status=500)
    return jsonify({"success": True, "data": job_to_dict(job)}), 201


@jobs_bp.get("")
def get_jobs():
    """Return a filtered, database-paginated job collection."""
    values, errors = _validate_list_query(request.args)
    if errors:
        return _error("VALIDATION_ERROR", "Invalid query parameters.", details=errors, status=400)

    try:
        jobs, total = list_jobs(**values)
    except JobDatabaseError:
        return _error("DATABASE_ERROR", "Unable to list jobs.", status=500)
    pages = math.ceil(total / values["per_page"]) if total else 0
    return jsonify(
        {
            "success": True,
            "data": [job_to_dict(job) for job in jobs],
            "pagination": {
                "page": values["page"],
                "per_page": values["per_page"],
                "total": total,
                "pages": pages,
            },
        }
    )


@jobs_bp.get("/<job_id>")
def get_job(job_id: str):
    """Return one job by UUID."""
    parsed_id, error = _parse_uuid(job_id)
    if error:
        return error
    try:
        job = get_job_by_id(parsed_id)
    except JobNotFoundError:
        return _error("JOB_NOT_FOUND", "Job not found.", status=404)
    except JobDatabaseError:
        return _error("DATABASE_ERROR", "Unable to retrieve job.", status=500)
    return jsonify({"success": True, "data": job_to_dict(job)})


@jobs_bp.post("/<job_id>/cancel")
def cancel_job_endpoint(job_id: str):
    """Cancel a not-yet-running job using a locked state transition."""
    parsed_id, error = _parse_uuid(job_id)
    if error:
        return error
    try:
        job = cancel_job(parsed_id)
    except JobNotFoundError:
        return _error("JOB_NOT_FOUND", "Job not found.", status=404)
    except JobStateConflictError as exc:
        code = "JOB_RUNNING" if exc.status == JobStatus.RUNNING else f"JOB_ALREADY_{exc.status.value}"
        return _error(code, str(exc), status=409)
    except JobDatabaseError:
        return _error("DATABASE_ERROR", "Unable to cancel job.", status=500)
    return jsonify({"success": True, "message": "Job cancelled successfully.", "data": job_to_dict(job)})


def _validate_job_input(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    errors: dict[str, str] = {}
    name = body.get("name")
    task_type = body.get("task_type")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
        errors["name"] = "Name must be a non-empty string of at most 255 characters."
    if not isinstance(task_type, str) or not task_type.strip() or len(task_type.strip()) > 255:
        errors["task_type"] = "Task type must be a non-empty string of at most 255 characters."

    payload = body.get("payload", {})
    if not isinstance(payload, dict):
        errors["payload"] = "Payload must be a JSON object."

    priority = body.get("priority", 5)
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0:
        errors["priority"] = "Priority must be a non-negative integer."

    max_retries = body.get("max_retries", 3)
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
        errors["max_retries"] = "Max retries must be a non-negative integer."

    scheduled_at, scheduled_error = _parse_scheduled_at(body.get("scheduled_at"))
    if scheduled_error:
        errors["scheduled_at"] = scheduled_error
    if errors:
        return {}, errors
    return {
        "name": name.strip(),
        "task_type": task_type.strip(),
        "payload": payload,
        "priority": priority,
        "max_retries": max_retries,
        "scheduled_at": scheduled_at,
    }, {}


def _parse_scheduled_at(value: Any) -> tuple[datetime | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, str) or not value.strip():
        return None, "Scheduled at must be an ISO-8601 datetime."
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None, "Scheduled at must be a valid ISO-8601 datetime."
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, "Scheduled at must include timezone information."
    return parsed.astimezone(timezone.utc), None


def _validate_list_query(args: Any) -> tuple[dict[str, Any], dict[str, str]]:
    errors: dict[str, str] = {}
    page = _parse_query_int(args.get("page", "1"), "page", errors)
    per_page = _parse_query_int(args.get("per_page", str(DEFAULT_PER_PAGE)), "per_page", errors)
    if page is not None and page < 1:
        errors["page"] = "Page must be at least 1."
    if per_page is not None and not 1 <= per_page <= MAX_PER_PAGE:
        errors["per_page"] = f"Per page must be between 1 and {MAX_PER_PAGE}."

    status = None
    if args.get("status") is not None:
        try:
            status = JobStatus(args["status"].upper())
        except ValueError:
            errors["status"] = "Status is invalid."

    priority = None
    if args.get("priority") is not None:
        priority = _parse_query_int(args["priority"], "priority", errors)
        if priority is not None and priority < 0:
            errors["priority"] = "Priority must be non-negative."

    task_type = args.get("task_type")
    if task_type == "":
        errors["task_type"] = "Task type must not be empty."
    if errors:
        return {}, errors
    return {"page": page, "per_page": per_page, "status": status, "task_type": task_type, "priority": priority}, {}


def _parse_query_int(value: str, field: str, errors: dict[str, str]) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        errors[field] = f"{field.replace('_', ' ').capitalize()} must be an integer."
        return None


def _parse_uuid(value: str) -> tuple[uuid.UUID | None, Any | None]:
    try:
        return uuid.UUID(value), None
    except (ValueError, AttributeError):
        return None, _error("VALIDATION_ERROR", "Job ID must be a valid UUID.", status=400)


def _error(code: str, message: str, *, details: dict[str, str] | None = None, status: int):
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return jsonify({"success": False, "error": error}), status
