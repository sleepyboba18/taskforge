"""Authenticated operational monitoring endpoints."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from app.auth.decorators import require_roles
from app.auth.permissions import OPERATORS
from app.rate_limit import rate_limit
from app.services.monitoring_service import MonitoringDatabaseError, get_monitoring_snapshot, validate_window

monitoring_bp = Blueprint("monitoring", __name__, url_prefix="/api/v1/monitoring")


def _error(code: str, message: str, status: int):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status


def _snapshot():
    try:
        return get_monitoring_snapshot(
            window=validate_window(request.args.get("window")),
            settings=current_app.config["TASKFORGE_SETTINGS"],
        )
    except ValueError as exc:
        return _error("VALIDATION_ERROR", str(exc), 400)
    except MonitoringDatabaseError:
        current_app.logger.exception("monitoring_snapshot_failed")
        return _error("MONITORING_UNAVAILABLE", "Monitoring data is temporarily unavailable.", 503)


@monitoring_bp.get("/overview")
@require_roles(*OPERATORS)
@rate_limit("read")
def monitoring_overview():
    snapshot = _snapshot()
    if isinstance(snapshot, tuple):
        return snapshot
    return jsonify({"success": True, "data": snapshot})


def _category(name: str):
    snapshot = _snapshot()
    if isinstance(snapshot, tuple):
        return snapshot
    return jsonify({"success": True, "data": snapshot[name]})


@monitoring_bp.get("/queue")
@require_roles(*OPERATORS)
@rate_limit("read")
def monitoring_queue():
    return _category("queue")


@monitoring_bp.get("/workers")
@require_roles(*OPERATORS)
@rate_limit("read")
def monitoring_workers():
    return _category("workers")


@monitoring_bp.get("/jobs")
@require_roles(*OPERATORS)
@rate_limit("read")
def monitoring_jobs():
    return _category("jobs")


@monitoring_bp.get("/workflows")
@require_roles(*OPERATORS)
@rate_limit("read")
def monitoring_workflows():
    return _category("workflows")


@monitoring_bp.get("/scheduler")
@require_roles(*OPERATORS)
@rate_limit("read")
def monitoring_scheduler():
    return _category("scheduler")


@monitoring_bp.get("/dlq")
@require_roles(*OPERATORS)
@rate_limit("read")
def monitoring_dlq():
    return _category("dlq")


@monitoring_bp.get("/database")
@require_roles(*OPERATORS)
@rate_limit("read")
def monitoring_database():
    return _category("database")


@monitoring_bp.get("/alerts")
@require_roles(*OPERATORS)
@rate_limit("read")
def monitoring_alerts():
    snapshot = _snapshot()
    if isinstance(snapshot, tuple):
        return snapshot
    return jsonify({"success": True, "data": {"alerts": snapshot["alerts"], "status": snapshot["status"]}})
