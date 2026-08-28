"""HTTP API blueprints."""

from flask import Blueprint, jsonify
from app.auth.decorators import require_roles
from app.auth.permissions import AUTHENTICATED
from app.rate_limit import rate_limit

api_bp = Blueprint("api", __name__)


@api_bp.get("/health")
def health_check():
    """Return a minimal service health response."""
    return jsonify({"status": "ok", "service": "TaskForge"})


from app.api.jobs import jobs_bp
from app.api.recurring_jobs import recurring_jobs_bp
from app.api.dead_letters import dead_letters_bp
from app.api.workers import workers_bp
from app.api.auth import auth_bp
from app.api.users import users_bp
from app.api.observability import observability_bp
from app.api.workflows import workflows_bp
from app.api.audit import audit_bp, history_endpoint
from app.api.monitoring import monitoring_bp
from app.api.admin import admin_bp

api_bp.register_blueprint(jobs_bp)
api_bp.register_blueprint(recurring_jobs_bp)
api_bp.register_blueprint(dead_letters_bp)
api_bp.register_blueprint(workers_bp)
api_bp.register_blueprint(auth_bp)
api_bp.register_blueprint(users_bp)
api_bp.register_blueprint(observability_bp)
api_bp.register_blueprint(workflows_bp)
api_bp.register_blueprint(audit_bp)
api_bp.register_blueprint(monitoring_bp)
api_bp.register_blueprint(admin_bp)


@api_bp.get("/api/v1/jobs/<job_id>/history")
@require_roles(*AUTHENTICATED)
@rate_limit("read")
def job_history_endpoint(job_id: str):
    return history_endpoint(field="job_id", value=job_id)


@api_bp.get("/api/v1/workflows/<workflow_id>/history")
@require_roles(*AUTHENTICATED)
@rate_limit("read")
def workflow_history_endpoint(workflow_id: str):
    return history_endpoint(field="workflow_id", value=workflow_id)


__all__ = ["api_bp", "auth_bp", "dead_letters_bp", "jobs_bp", "observability_bp", "recurring_jobs_bp", "users_bp", "workers_bp"]
