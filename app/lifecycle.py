"""Centralized application lifecycle management."""

from __future__ import annotations

import logging
import time
from enum import Enum
from threading import Lock
from typing import Any

logger = logging.getLogger("taskforge.lifecycle")


class LifecycleState(str, Enum):
    """Application lifecycle state."""

    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class LifecycleManager:
    """Centralized application lifecycle state and shutdown coordination."""

    def __init__(self):
        self._state = LifecycleState.STARTING
        self._lock = Lock()
        self._startup_time = time.monotonic()
        self._shutdown_requested = False
        self._startup_error: Exception | None = None

    @property
    def state(self) -> LifecycleState:
        """Current lifecycle state."""
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        """Whether the application is in RUNNING state."""
        return self.state == LifecycleState.RUNNING

    @property
    def is_stopping(self) -> bool:
        """Whether the application is in STOPPING or later state."""
        state = self.state
        return state in {LifecycleState.STOPPING, LifecycleState.STOPPED, LifecycleState.FAILED}

    @property
    def shutdown_requested(self) -> bool:
        """Whether shutdown has been explicitly requested."""
        with self._lock:
            return self._shutdown_requested

    def mark_running(self) -> None:
        """Transition to RUNNING state after successful initialization."""
        with self._lock:
            if self._state == LifecycleState.STARTING:
                self._state = LifecycleState.RUNNING
                startup_duration = time.monotonic() - self._startup_time
                logger.info("Application started successfully in %.2f seconds", startup_duration)

    def mark_stopping(self) -> None:
        """Transition to STOPPING state when shutdown begins."""
        with self._lock:
            if self._state == LifecycleState.RUNNING:
                self._state = LifecycleState.STOPPING
                self._shutdown_requested = True
            logger.info("Application shutdown initiated")

    def mark_stopped(self) -> None:
        """Transition to STOPPED state after cleanup completes."""
        with self._lock:
            if self._state in {LifecycleState.STOPPING, LifecycleState.FAILED}:
                self._state = LifecycleState.STOPPED

    def mark_failed(self, error: Exception) -> None:
        """Record startup or runtime failure."""
        with self._lock:
            self._startup_error = error
            if self._state == LifecycleState.STARTING:
                self._state = LifecycleState.FAILED
                logger.error("Application startup failed: %s", type(error).__name__)
            elif self._state == LifecycleState.RUNNING:
                self._state = LifecycleState.FAILED
                logger.error("Application runtime failure: %s", type(error).__name__)

    def startup_duration_seconds(self) -> float:
        """Seconds elapsed since startup began."""
        return time.monotonic() - self._startup_time

    def to_dict(self) -> dict[str, Any]:
        """Serialize lifecycle state for monitoring."""
        return {
            "state": self.state,
            "uptime_seconds": round(self.startup_duration_seconds(), 2),
            "is_running": self.is_running,
            "is_stopping": self.is_stopping,
        }


# Global singleton
_lifecycle: LifecycleManager | None = None
_lifecycle_lock = Lock()


def get_lifecycle() -> LifecycleManager:
    """Get or create the global lifecycle manager."""
    global _lifecycle
    if _lifecycle is None:
        with _lifecycle_lock:
            if _lifecycle is None:
                _lifecycle = LifecycleManager()
    return _lifecycle
