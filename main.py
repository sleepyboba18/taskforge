from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from app import create_app, socketio
from app.config.settings import ConfigurationError, Settings
from app.lifecycle import get_lifecycle
from app.sockets import broadcast_server_shutdown
from app.workers import WorkerManager
from app.workers.signals import install_shutdown_handlers
from app.database.session import (
    check_database_connection,
    dispose_database,
    initialize_database,
    initialize_schema,
)
from app.services.user_service import bootstrap_admin, UserServiceError

logger = logging.getLogger("taskforge")

def main() -> None:
    lifecycle = get_lifecycle()
    try:
        settings = Settings.from_environment()
        initialize_database(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
        )
        check_database_connection()
        app = create_app(settings)
    except ConfigurationError as exc:
        logging.basicConfig(level=logging.ERROR)
        logger.error("Configuration error: %s", exc)
        raise SystemExit(1) from exc
    except SQLAlchemyError as exc:
        lifecycle.mark_failed(exc)
        logger.error("Database connectivity check failed during startup: %s", type(exc).__name__)
        dispose_database()
        raise SystemExit(1) from None
    except Exception as exc:
        lifecycle.mark_failed(exc)
        logger.error("Application initialization failed: %s", type(exc).__name__)
        dispose_database()
        raise SystemExit(1) from None

    worker_manager = None
    try:
        worker_manager = WorkerManager(settings)
        if settings.bootstrap_admin_username:
            try:
                initialize_schema()
                bootstrap_admin(
                    username=settings.bootstrap_admin_username,
                    email=settings.bootstrap_admin_email,
                    password=settings.bootstrap_admin_password,
                )
            except (UserServiceError, SQLAlchemyError) as exc:
                logger.error("Bootstrap administrator setup failed: %s", type(exc).__name__)
                raise SystemExit(1) from exc

        install_shutdown_handlers(worker_manager.stop)
        worker_manager.start()
        logger.info("Starting %s on %s:%s", settings.app_name, settings.host, settings.port)
        socketio.run(
            app,
            host=settings.host,
            port=settings.port,
            debug=settings.debug,
            use_reloader=False,
        )
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as exc:
        lifecycle.mark_failed(exc)
        logger.error("Application startup or runtime failed: %s", type(exc).__name__)
        raise SystemExit(1) from None
    finally:
        lifecycle.mark_stopping()
        try:
            broadcast_server_shutdown()
        finally:
            try:
                if worker_manager is not None:
                    worker_manager.stop()
            finally:
                try:
                    dispose_database()
                finally:
                    lifecycle.mark_stopped()

if __name__ == "__main__":
    main()
