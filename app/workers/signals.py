"""Signal helpers for application shutdown orchestration."""

from __future__ import annotations

import logging
import signal

logger = logging.getLogger("taskforge.signals")


def install_shutdown_handlers(shutdown_callback) -> None:
    """Have SIGINT and SIGTERM request the same graceful shutdown path."""
    def handle_shutdown(signum, _frame):
        logger.info("Received shutdown signal: %s", signum)
        shutdown_callback()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_shutdown)
