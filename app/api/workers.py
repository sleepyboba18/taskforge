"""Worker health and registration visibility endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from flask import Blueprint, jsonify, request

from app.api.serializers import worker_to_dict
from app.auth.decorators import require_roles
from app.auth.permissions import AUTHENTICATED
from app.config.settings import Settings
from app.models import WorkerStatus
from app.services.worker_service import WorkerDatabaseError, get_worker, list_workers, worker_health

workers_bp = Blueprint("workers", __name__, url_prefix="/api/v1/workers")


@workers_bp.get("")
@require_roles(*AUTHENTICATED)
def list_workers_endpoint():
    value = request.args.get("status")
    status = None
    if value is not None:
        try:
            status = WorkerStatus(value.upper())
        except ValueError:
            return _error("VALIDATION_ERROR", "Worker status is invalid.", 400)
    try:
        workers = list_workers(status=status)
    except WorkerDatabaseError:
        return _error("DATABASE_ERROR", "Unable to list workers.", 500)
    return jsonify({"success": True, "data": [worker_to_dict(worker) for worker in workers]})


@workers_bp.get("/health")
@require_roles(*AUTHENTICATED)
def worker_health_endpoint():
    try:
        summary = worker_health(stale_timeout=Settings.from_environment().worker_stale_timeout)
    except WorkerDatabaseError:
        return _error("DATABASE_ERROR", "Unable to read worker health.", 500)
    return jsonify({"success": True, "data": summary})


@workers_bp.get("/<worker_id>")
@require_roles(*AUTHENTICATED)
def get_worker_endpoint(worker_id: str):
    try:
        parsed_id = uuid.UUID(worker_id)
    except (ValueError, AttributeError):
        return _error("VALIDATION_ERROR", "Worker ID must be a valid UUID.", 400)
    try:
        worker = get_worker(parsed_id)
    except WorkerDatabaseError:
        return _error("DATABASE_ERROR", "Unable to retrieve worker.", 500)
    if worker is None:
        return _error("WORKER_NOT_FOUND", "Worker not found.", 404)
    return jsonify({"success": True, "data": worker_to_dict(worker)})


def _error(code: str, message: str, status: int):
    return jsonify({"success": False, "error": {"code": code, "message": message}}), status
