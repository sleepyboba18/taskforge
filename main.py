from __future__ import annotations

import logging

from app import create_app, socketio
from app.config.settings import ConfigurationError, Settings
from app.lifecycle import get_lifecycle
from app.sockets import broadcast_server_shutdown
from app.workers import WorkerManager
from app.workers.signals import install_shutdown_handlers
from app.database.session import initialize_schema, dispose_database
from app.services.user_service import bootstrap_admin, UserServiceError

logger = logging.getLogger("taskforge")

def main() -> None:
    try:
        settings = Settings.from_environment()
        app = create_app(settings)
    except ConfigurationError as exc:
        logging.basicConfig(level=logging.ERROR)
        logger.error("Configuration error: %s", exc)
        raise SystemExit(1) from exc

    if settings.bootstrap_admin_username:
        try:
            initialize_schema()
            bootstrap_admin(
                username=settings.bootstrap_admin_username,
                email=settings.bootstrap_admin_email,
                password=settings.bootstrap_admin_password,
            )
        except UserServiceError as exc:
            logger.error("Bootstrap administrator setup failed: %s", type(exc).__name__)
            raise SystemExit(1) from exc

    worker_manager = WorkerManager(settings)
    lifecycle = get_lifecycle()
    install_shutdown_handlers(worker_manager.stop)
    worker_manager.start()
    logger.info("Starting %s on %s:%s", settings.app_name, settings.host, settings.port)
    try:
        socketio.run(
            app,
            host=settings.host,
            port=settings.port,
            debug=settings.debug,
            use_reloader=False,
        )
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    finally:
        lifecycle.mark_stopping()
        broadcast_server_shutdown()
        worker_manager.stop()
        dispose_database()
        lifecycle.mark_stopped()

if __name__ == "__main__":
    main()
