"""Reusable API rate-limit decorator."""

from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import current_app, g, jsonify, make_response, request

from app.models import UserRole
from app.rate_limit.service import (
    RateLimitUnavailableError,
    consume,
    identity_key,
    role_limit,
)


def rate_limit(category: str):
    """Apply a PostgreSQL-backed fixed-window policy to one external route."""
    if category not in {"auth", "read", "write", "admin"}:
        raise ValueError("Unknown rate-limit category.")

    def decorator(view: Callable):
        @wraps(view)
        def wrapped(*args, **kwargs):
            settings = current_app.config["TASKFORGE_SETTINGS"]
            if not settings.rate_limit_enabled:
                return view(*args, **kwargs)

            user = getattr(g, "current_user", None)
            if category == "auth":
                limit = settings.login_rate_limit_requests
                window_seconds = settings.login_rate_limit_window_seconds
            else:
                limit = role_limit(
                    user.role if user else None,
                    settings.rate_limit_requests,
                    settings.rate_limit_admin,
                    settings.rate_limit_operator,
                    settings.rate_limit_viewer,
                )
                window_seconds = settings.rate_limit_window_seconds
            endpoint = request.url_rule.rule if request.url_rule else request.path
            key = identity_key(
                user_id=user.id if user else None,
                client_ip=request.remote_addr or "unknown",
                endpoint=f"{request.method}:{category}:{endpoint}",
            )
            try:
                decision = consume(
                    key=key,
                    limit=limit,
                    window_seconds=window_seconds,
                    fail_open=settings.rate_limit_fail_open,
                )
            except RateLimitUnavailableError:
                return _error("rate_limit_unavailable", "Rate limiting is temporarily unavailable.", 503)

            if not decision.allowed:
                current_app.logger.warning(
                    "rate_limit_exceeded",
                    extra={
                        "request_id": getattr(g, "request_id", None),
                        "method": request.method,
                        "path": request.path,
                        "limit": decision.limit,
                        "window": window_seconds,
                        "user_id": str(user.id) if user else None,
                    },
                )
                response = _error("rate_limit_exceeded", "Too many requests. Please try again later.", 429)
                response[0].headers.update(_headers(decision))
                response[0].headers["Retry-After"] = str(decision.retry_after)
                return response

            response = make_response(view(*args, **kwargs))
            response.headers.update(_headers(decision))
            return response
        return wrapped
    return decorator


def _headers(decision):
    return {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
        "X-RateLimit-Reset": str(decision.reset_at),
    }


def _error(code: str, message: str, status: int):
    return jsonify({"success": False, "error": {"code": code, "message": message}, "request_id": getattr(g, "request_id", None)}), status
