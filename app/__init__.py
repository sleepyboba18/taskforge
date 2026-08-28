"""TaskForge application factory and extension setup."""

from __future__ import annotations

import logging
from typing import Any

from flask import Flask, jsonify
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
        return jsonify({"error": error.name}), status_code

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        logger.exception("Unexpected application error: %s", type(error).__name__)
        return jsonify({"error": "Internal Server Error"}), 500


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
    logger.info("Configured %s application in %s mode", settings.app_name, settings.app_env)
    return app


__all__ = ["ConfigurationError", "create_app", "socketio"]
