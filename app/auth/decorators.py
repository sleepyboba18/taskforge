"""Reusable authentication and role decorators."""

from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import g, jsonify

from app.auth.jwt import authenticate_request, set_current_user
from app.models import User, UserRole


def _auth_error(code: str, status: int = 401):
    return jsonify({"success": False, "error": {"code": code}}), status


def require_authentication(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user, error = authenticate_request()
        if error:
            return _auth_error(error)
        set_current_user(user)
        return view(*args, **kwargs)
    return wrapped


def require_roles(*roles: UserRole):
    def decorator(view: Callable):
        @wraps(view)
        @require_authentication
        def wrapped(*args, **kwargs):
            user: User = g.current_user
            if user.role not in roles:
                return _auth_error("forbidden", 403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def require_role(role: UserRole):
    return require_roles(role)
