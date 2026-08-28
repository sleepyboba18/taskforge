"""Workflow management endpoints."""

from __future__ import annotations

import math
import uuid

from flask import Blueprint, current_app, g, jsonify, request

from app.api.serializers import job_to_dict
from app.auth.decorators import require_roles
from app.auth.permissions import AUTHENTICATED, OPERATORS
from app.models import WorkflowStatus
from app.rate_limit import rate_limit
from app.services.workflow_service import (
    WorkflowConflictError,
    WorkflowDatabaseError,
    WorkflowNotFoundError,
    cancel_workflow,
    create_workflow,
    get_workflow,
    list_workflow_jobs,
    list_workflows,
    retry_workflow,
    workflow_graph,
    workflow_summary,
)

workflows_bp = Blueprint("workflows", __name__, url_prefix="/api/v1/workflows")
MAX_PER_PAGE = 100


def _error(code: str, message: str, status: int):
    return jsonify({"success": False, "error": {"code": code, "message": message}, "request_id": getattr(g, "request_id", None)}), status


def _uuid(value: str):
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def _page():
    try:
        page, per_page = int(request.args.get("page", 1)), int(request.args.get("per_page", 20))
    except ValueError:
        return None
    return (page, per_page) if page >= 1 and 1 <= per_page <= MAX_PER_PAGE else None


def _workflow_dict(workflow, summary=None):
    result = {"id": str(workflow.id), "name": workflow.name, "description": workflow.description, "status": workflow.status.value, "created_by": str(workflow.created_by) if workflow.created_by else None, "created_at": workflow.created_at.isoformat() if workflow.created_at else None, "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None, "started_at": workflow.started_at.isoformat() if workflow.started_at else None, "completed_at": workflow.completed_at.isoformat() if workflow.completed_at else None}
    if summary is not None:
        result["job_summary"] = summary
    return result


@workflows_bp.post("")
@require_roles(*OPERATORS)
@rate_limit("write")
def create_workflow_endpoint():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - {"name", "description"} or not isinstance(body.get("name"), str) or not body["name"].strip() or len(body["name"].strip()) > 255:
        return _error("VALIDATION_ERROR", "Name is required and must be at most 255 characters.", 400)
    description = body.get("description")
    if description is not None and (not isinstance(description, str) or len(description) > 4000):
        return _error("VALIDATION_ERROR", "Description must be a string of at most 4000 characters.", 400)
    try:
        workflow = create_workflow(name=body["name"].strip(), description=description, created_by=g.current_user.id)
    except WorkflowDatabaseError:
        return _error("DATABASE_ERROR", "Unable to create workflow.", 500)
    return jsonify({"success": True, "data": _workflow_dict(workflow)}), 201


@workflows_bp.get("")
@require_roles(*AUTHENTICATED)
@rate_limit("read")
def list_workflows_endpoint():
    page_data = _page()
    if page_data is None:
        return _error("VALIDATION_ERROR", "Invalid pagination.", 400)
    page, per_page = page_data
    try:
        workflows, total = list_workflows(page=page, per_page=per_page, status=WorkflowStatus(request.args["status"].upper()) if request.args.get("status") else None)
    except ValueError:
        return _error("VALIDATION_ERROR", "Invalid workflow status.", 400)
    except WorkflowDatabaseError:
        return _error("DATABASE_ERROR", "Unable to list workflows.", 500)
    return jsonify({"success": True, "data": [_workflow_dict(workflow) for workflow in workflows], "pagination": {"page": page, "per_page": per_page, "total": total, "pages": math.ceil(total / per_page) if total else 0}})


@workflows_bp.get("/<workflow_id>")
@require_roles(*AUTHENTICATED)
@rate_limit("read")
def workflow_detail_endpoint(workflow_id: str):
    parsed = _uuid(workflow_id)
    if parsed is None:
        return _error("WORKFLOW_NOT_FOUND", "Workflow not found.", 404)
    try:
        workflow = get_workflow(parsed)
        summary = workflow_summary(parsed)
    except WorkflowNotFoundError:
        return _error("WORKFLOW_NOT_FOUND", "Workflow not found.", 404)
    except WorkflowDatabaseError:
        return _error("DATABASE_ERROR", "Unable to retrieve workflow.", 500)
    return jsonify({"success": True, "data": _workflow_dict(workflow, summary)})


@workflows_bp.get("/<workflow_id>/jobs")
@require_roles(*AUTHENTICATED)
@rate_limit("read")
def workflow_jobs_endpoint(workflow_id: str):
    parsed, page_data = _uuid(workflow_id), _page()
    if parsed is None or page_data is None:
        return _error("VALIDATION_ERROR", "Invalid workflow ID or pagination.", 400)
    try:
        jobs, total = list_workflow_jobs(parsed, page=page_data[0], per_page=page_data[1])
    except WorkflowNotFoundError:
        return _error("WORKFLOW_NOT_FOUND", "Workflow not found.", 404)
    except WorkflowDatabaseError:
        return _error("DATABASE_ERROR", "Unable to list workflow jobs.", 500)
    return jsonify({"success": True, "data": [job_to_dict(job) for job in jobs], "pagination": {"page": page_data[0], "per_page": page_data[1], "total": total}})


@workflows_bp.get("/<workflow_id>/graph")
@require_roles(*AUTHENTICATED)
@rate_limit("read")
def workflow_graph_endpoint(workflow_id: str):
    parsed = _uuid(workflow_id)
    if parsed is None:
        return _error("WORKFLOW_NOT_FOUND", "Workflow not found.", 404)
    settings = current_app.config["TASKFORGE_SETTINGS"]
    try:
        graph = workflow_graph(parsed, max_depth=settings.max_dependency_graph_depth, max_nodes=settings.max_dependency_graph_nodes)
    except WorkflowNotFoundError:
        return _error("WORKFLOW_NOT_FOUND", "Workflow not found.", 404)
    except WorkflowDatabaseError:
        return _error("DATABASE_ERROR", "Unable to retrieve workflow graph.", 500)
    return jsonify({"success": True, "data": graph})


@workflows_bp.post("/<workflow_id>/cancel")
@require_roles(*OPERATORS)
@rate_limit("write")
def cancel_workflow_endpoint(workflow_id: str):
    return _workflow_action(workflow_id, cancel_workflow, "cancelled")


@workflows_bp.post("/<workflow_id>/retry")
@require_roles(*OPERATORS)
@rate_limit("write")
def retry_workflow_endpoint(workflow_id: str):
    return _workflow_action(workflow_id, retry_workflow, "retrying")


def _workflow_action(workflow_id, action, result):
    parsed = _uuid(workflow_id)
    if parsed is None:
        return _error("WORKFLOW_NOT_FOUND", "Workflow not found.", 404)
    try:
        workflow = action(parsed)
    except WorkflowNotFoundError:
        return _error("WORKFLOW_NOT_FOUND", "Workflow not found.", 404)
    except WorkflowConflictError:
        return _error("WORKFLOW_CONFLICT", "Workflow is not eligible for this operation.", 409)
    except WorkflowDatabaseError:
        return _error("DATABASE_ERROR", "Unable to update workflow.", 500)
    return jsonify({"success": True, "message": f"Workflow {result}.", "data": _workflow_dict(workflow)})


