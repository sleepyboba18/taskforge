"""TaskForge executable entry point."""

from __future__ import annotations

import logging

from app import create_app, socketio
from app.config.settings import ConfigurationError, Settings
from app.workers import WorkerManager
from app.workers.signals import install_shutdown_handlers

logger = logging.getLogger("taskforge")


def main() -> None:
    try:
        settings = Settings.from_environment()
        app = create_app(settings)
    except ConfigurationError as exc:
        logging.basicConfig(level=logging.ERROR)
        logger.error("Configuration error: %s", exc)
        raise SystemExit(1) from exc

    worker_manager = WorkerManager(settings)
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
        worker_manager.stop()


if __name__ == "__main__":
    main()
