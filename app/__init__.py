"""TaskForge application factory and extension setup."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from flask import Flask, g, jsonify, request
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

from app.api import api_bp
from app.config.settings import ConfigurationError, Settings
from app.database.session import initialize_database
from app.sockets import socketio

logger = logging.getLogger("taskforge")


def _configure_logging() -> None:
    """Configure the application logger once for local and worker use."""
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )


def _register_error_handlers(app: Flask) -> None:
    """Return consistent JSON responses without exposing internal details."""

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        status_code = error.code or 500
        return jsonify({"success": False, "error": {"code": error.name.lower().replace(" ", "_"), "message": error.name}, "request_id": getattr(g, "request_id", None)}), status_code

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        logger.exception("Unexpected application error: %s", type(error).__name__)
        return jsonify({"success": False, "error": {"code": "internal_server_error", "message": "Internal Server Error"}, "request_id": getattr(g, "request_id", None)}), 500


def create_app(settings: Settings | None = None) -> Flask:
    """Create and configure a TaskForge Flask application."""
    _configure_logging()
    settings = settings or Settings.from_environment()
    from app import models  # noqa: F401  # Register ORM models with Base.metadata.

    app = Flask(__name__)
    app.config.from_mapping(settings.as_flask_config())
    app.config["TASKFORGE_SETTINGS"] = settings

    cors_origins: str | list[str] = settings.cors_origins
    CORS(app, origins=cors_origins, supports_credentials=settings.cors_supports_credentials)
    socketio.init_app(app, cors_allowed_origins=cors_origins)
    initialize_database(settings.database_url)

    app.register_blueprint(api_bp)
    _register_error_handlers(app)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        request_id = getattr(g, "request_id", None)
        if request_id:
            response.headers["X-Request-ID"] = request_id
        started_at = getattr(g, "request_started_at", None)
        if started_at is not None:
            duration_ms = (time.monotonic() - started_at) * 1000
            user = getattr(g, "current_user", None)
            log_method = logger.warning if duration_ms >= settings.slow_request_threshold_ms else logger.info
            log_method(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.path,
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                    "user_id": str(user.id) if user else None,
                    "role": user.role.value if user else None,
                },
            )
        return response

    @app.before_request
    def initialize_request_context():
        supplied_id = request.headers.get("X-Request-ID", "").strip()
        try:
            request_id = str(uuid.UUID(supplied_id)) if supplied_id else str(uuid.uuid4())
        except ValueError:
            request_id = str(uuid.uuid4())
        g.request_id = request_id
        g.request_started_at = time.monotonic()

    logger.info("Configured %s application in %s mode", settings.app_name, settings.app_env)
    return app


__all__ = ["ConfigurationError", "create_app", "socketio"]
