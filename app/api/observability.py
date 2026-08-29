"""Operational health and metrics endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify, current_app, request

from app.auth.decorators import require_roles
from app.auth.permissions import AUTHENTICATED
from app.lifecycle import get_lifecycle
from app.rate_limit import rate_limit
from app.database.session import check_database_connection
from app.services.observability_service import (
    ObservabilityDatabaseError,
    collect_metrics,
    detailed_health,
)

observability_bp = Blueprint("observability", __name__)


@observability_bp.get("/ready")
def readiness():
    """Report whether the application can reach PostgreSQL and is not shutting down."""
    if get_lifecycle().is_stopping:
        return jsonify({"status": "not_ready"}), 503
    try:
        check_database_connection()
    except Exception:
        return jsonify({"status": "not_ready"}), 503
    return jsonify({"status": "ready"})


@observability_bp.get("/api/v1/health")
@require_roles(*AUTHENTICATED)
@rate_limit("read")
def detailed_health_endpoint():
    settings = current_app.config["TASKFORGE_SETTINGS"]
    return jsonify({"success": True, "data": detailed_health(stale_timeout=settings.worker_stale_timeout)})


@observability_bp.get("/api/v1/metrics")
@require_roles(*AUTHENTICATED)
@rate_limit("read")
def metrics_endpoint():
    settings = current_app.config["TASKFORGE_SETTINGS"]
    window = request.args.get("window", settings.metrics_default_window)
    if window not in {"1h", "24h", "7d"}:
        return jsonify({"success": False, "error": {"code": "VALIDATION_ERROR", "message": "window must be 1h, 24h, or 7d"}}), 400
    try:
        metrics = collect_metrics(window=window)
    except ObservabilityDatabaseError:
        return jsonify({"success": False, "error": {"code": "DATABASE_ERROR", "message": "Unable to collect metrics"}}), 500
    metrics["rate_limiting"] = {"enabled": settings.rate_limit_enabled}
    return jsonify({"success": True, "data": metrics})
