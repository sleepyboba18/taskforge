"""Explicitly registered, safe task handlers for the worker engine."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class UnknownTaskError(LookupError):
    """Raised when a job refers to an unregistered task type."""


class TaskValidationError(ValueError):
    """Raised when a built-in task receives invalid data."""


class NonRetryableTaskError(RuntimeError):
    """Raised by a task that must fail without scheduling a retry."""


class RetryableTaskError(RuntimeError):
    """Raised by a task that explicitly permits the normal retry policy."""


TaskHandler = Callable[[dict[str, Any]], Any]


class TaskRegistry:
    """Allow execution only for handlers explicitly registered by the application."""

    def __init__(self) -> None:
        self._handlers: dict[str, TaskHandler] = {}

    def register(self, task_type: str, handler: TaskHandler) -> None:
        if not task_type or not callable(handler):
            raise ValueError("A task type and callable handler are required.")
        self._handlers[task_type] = handler

    def execute(self, task_type: str, payload: dict[str, Any]) -> Any:
        handler = self._handlers.get(task_type)
        if handler is None:
            raise UnknownTaskError(f"Task type is not registered: {task_type}")
        return handler(payload)

    @classmethod
    def builtins(cls) -> "TaskRegistry":
        registry = cls()
        registry.register("echo", _echo)
        registry.register("sleep", _sleep)
        return registry


def _echo(payload: dict[str, Any]) -> dict[str, Any]:
    return payload


def _sleep(payload: dict[str, Any]) -> dict[str, float]:
    seconds = payload.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 <= seconds <= 10:
        raise TaskValidationError("sleep.seconds must be a number between 0 and 10.")
    time.sleep(seconds)
    return {"slept": float(seconds)}
