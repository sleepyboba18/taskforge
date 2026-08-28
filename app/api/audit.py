"""Read-only audit and history endpoints."""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from app.auth.decorators import require_roles
from app.auth.permissions import OPERATORS
from app.models import AuditEntityType, AuditEventType
from app.rate_limit import rate_limit
from app.services.audit_service import AuditDatabaseError, get_event, list_events


audit_bp = Blueprint("audit", __name__, url_prefix="/api/v1/audit-events")
MAX_PER_PAGE = 100


def _error(code: str, message: str, status: int):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def _page():
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
    except ValueError:
        return None
    return (page, per_page) if page >= 1 and 1 <= per_page <= MAX_PER_PAGE else None


def _parse_filters():
    filters = {}
    for field in ("entity_id", "job_id", "workflow_id", "actor_id", "worker_id"):
        value = request.args.get(field)
        if value:
            try:
                filters[field] = uuid.UUID(value)
            except ValueError:
                return None
    for field, enum_type in (("event_type", AuditEventType), ("entity_type", AuditEntityType)):
        value = request.args.get(field)
        if value:
            try:
                filters[field] = enum_type(value.upper())
            except ValueError:
                return None
    for field in ("created_after", "created_before"):
        value = request.args.get(field)
        if value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    return None
                filters[field] = parsed.astimezone(timezone.utc)
            except ValueError:
                return None
    return filters


@audit_bp.get("")
@require_roles(*OPERATORS)
@rate_limit("admin")
def audit_events_endpoint():
    page_data = _page()
    filters = _parse_filters()
    if page_data is None or filters is None:
        return _error("VALIDATION_ERROR", "Invalid pagination or audit filter.", 400)
    try:
        items, total = list_events(page=page_data[0], per_page=page_data[1], filters=filters)
    except AuditDatabaseError:
        return _error("DATABASE_ERROR", "Unable to retrieve audit events.", 500)
    return jsonify({"success": True, "data": items, "pagination": {"page": page_data[0], "per_page": page_data[1], "total": total, "pages": math.ceil(total / page_data[1]) if total else 0}})


@audit_bp.get("/<audit_event_id>")
@require_roles(*OPERATORS)
@rate_limit("admin")
def audit_event_detail_endpoint(audit_event_id: str):
    try:
        event_id = uuid.UUID(audit_event_id)
    except ValueError:
        return _error("AUDIT_EVENT_NOT_FOUND", "Audit event not found.", 404)
    try:
        event = get_event(event_id)
    except KeyError:
        return _error("AUDIT_EVENT_NOT_FOUND", "Audit event not found.", 404)
    except AuditDatabaseError:
        return _error("DATABASE_ERROR", "Unable to retrieve audit event.", 500)
    return jsonify({"success": True, "data": event})


def history_endpoint(*, field: str, value: str):
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return _error("RESOURCE_NOT_FOUND", "Resource not found.", 404)
    page_data = _page()
    if page_data is None:
        return _error("VALIDATION_ERROR", "Invalid pagination.", 400)
    try:
        items, total = list_events(page=page_data[0], per_page=page_data[1], filters={field: parsed})
    except AuditDatabaseError:
        return _error("DATABASE_ERROR", "Unable to retrieve history.", 500)
    return jsonify({"success": True, "data": items, "pagination": {"page": page_data[0], "per_page": page_data[1], "total": total, "pages": math.ceil(total / page_data[1]) if total else 0}})
