"""REST endpoints for dead-letter management."""

from __future__ import annotations

import math
import uuid
from typing import Any

from flask import Blueprint, jsonify, request

from app.api.serializers import dead_letter_to_dict, job_to_dict
from app.auth.decorators import require_roles
from app.auth.permissions import AUTHENTICATED, OPERATORS
from app.rate_limit import rate_limit
from app.services.dead_letter_service import (
    DeadLetterConflictError,
    DeadLetterDatabaseError,
    DeadLetterNotFoundError,
    delete_dead_letter,
    get_dead_letter_by_id,
    list_dead_letters,
    retry_dead_letter,
)


dead_letters_bp = Blueprint("dead_letters", __name__, url_prefix="/api/v1/dead-letters")
DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


@dead_letters_bp.get("")
@require_roles(*AUTHENTICATED)
@rate_limit("read")
def list_dead_letter_endpoint():
    page, per_page, errors = _pagination(request.args)
    if errors:
        return _error("VALIDATION_ERROR", "Invalid query parameters.", 400, errors)
    values: dict[str, Any] = {"page": page, "per_page": per_page}
    for field in ("job_id", "recurring_job_id"):
        value = request.args.get(field)
        if value is not None:
            try:
                values[field] = uuid.UUID(value)
            except ValueError:
                return _error("VALIDATION_ERROR", f"{field} must be a valid UUID.", 400)
        else:
            values[field] = None
    values["task_type"] = request.args.get("task_type") or None
    try:
        records, total = list_dead_letters(**values)
    except DeadLetterDatabaseError:
        return _error("DATABASE_ERROR", "Unable to list dead letters.", 500)
    return jsonify(
        {
            "success": True,
            "data": [dead_letter_to_dict(record) for record in records],
            "pagination": {
                "page": page, "per_page": per_page, "total": total,
                "pages": math.ceil(total / per_page) if total else 0,
            },
        }
    )


@dead_letters_bp.get("/<dead_letter_id>")
@require_roles(*AUTHENTICATED)
@rate_limit("read")
def get_dead_letter_endpoint(dead_letter_id: str):
    parsed_id, error = _parse_uuid(dead_letter_id)
    if error:
        return error
    try:
        record = get_dead_letter_by_id(parsed_id)
    except DeadLetterNotFoundError:
        return _error("DEAD_LETTER_NOT_FOUND", "Dead-letter record not found.", 404)
    except DeadLetterDatabaseError:
        return _error("DATABASE_ERROR", "Unable to retrieve dead letter.", 500)
    return jsonify({"success": True, "data": dead_letter_to_dict(record)})


@dead_letters_bp.post("/<dead_letter_id>/retry")
@require_roles(*OPERATORS)
@rate_limit("write")
def retry_dead_letter_endpoint(dead_letter_id: str):
    parsed_id, error = _parse_uuid(dead_letter_id)
    if error:
        return error
    try:
        job = retry_dead_letter(parsed_id)
    except DeadLetterNotFoundError:
        return _error("DEAD_LETTER_NOT_FOUND", "Dead-letter record not found.", 404)
    except DeadLetterConflictError:
        return _error("INVALID_RETRY_STATE", "Dead-letter job is not eligible for retry.", 409)
    except DeadLetterDatabaseError:
        return _error("DATABASE_ERROR", "Unable to retry dead-letter job.", 500)
    return jsonify({"success": True, "message": "Dead-letter job requeued.", "data": job_to_dict(job)})


@dead_letters_bp.delete("/<dead_letter_id>")
@require_roles(*OPERATORS)
@rate_limit("admin")
def delete_dead_letter_endpoint(dead_letter_id: str):
    parsed_id, error = _parse_uuid(dead_letter_id)
    if error:
        return error
    try:
        delete_dead_letter(parsed_id)
    except DeadLetterNotFoundError:
        return _error("DEAD_LETTER_NOT_FOUND", "Dead-letter record not found.", 404)
    except DeadLetterDatabaseError:
        return _error("DATABASE_ERROR", "Unable to delete dead-letter record.", 500)
    return jsonify({"success": True, "message": "Dead-letter record deleted."})


def _pagination(args: Any) -> tuple[int, int, dict[str, str]]:
    errors: dict[str, str] = {}
    try:
        page = int(args.get("page", 1))
    except (TypeError, ValueError):
        page = 0
        errors["page"] = "Page must be an integer."
    try:
        per_page = int(args.get("per_page", DEFAULT_PER_PAGE))
    except (TypeError, ValueError):
        per_page = 0
        errors["per_page"] = "Per page must be an integer."
    if page < 1:
        errors["page"] = "Page must be at least 1."
    if not 1 <= per_page <= MAX_PER_PAGE:
        errors["per_page"] = f"Per page must be between 1 and {MAX_PER_PAGE}."
    return page, per_page, errors


def _parse_uuid(value: str):
    try:
        return uuid.UUID(value), None
    except (ValueError, AttributeError):
        return None, _error("VALIDATION_ERROR", "Dead-letter ID must be a valid UUID.", 400)


def _error(code: str, message: str, status: int, details: dict[str, str] | None = None):
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return jsonify({"success": False, "error": error}), status
