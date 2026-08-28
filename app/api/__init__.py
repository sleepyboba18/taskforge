"""HTTP API blueprints."""

from flask import Blueprint, jsonify

api_bp = Blueprint("api", __name__)


@api_bp.get("/health")
def health_check():
    """Return a minimal service health response."""
    return jsonify({"status": "ok", "service": "TaskForge"})


from app.api.jobs import jobs_bp
from app.api.recurring_jobs import recurring_jobs_bp

api_bp.register_blueprint(jobs_bp)
api_bp.register_blueprint(recurring_jobs_bp)


__all__ = ["api_bp", "jobs_bp", "recurring_jobs_bp"]
