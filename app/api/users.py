"""Administrator-only user management endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from flask import Blueprint, jsonify, request

from app.auth.decorators import require_role
from app.auth.password import validate_password
from app.rate_limit import rate_limit
from app.models import UserRole
from app.services.user_service import (
    UserAlreadyExistsError,
    UserDatabaseError,
    UserNotFoundError,
    create_user,
    get_user,
    list_users,
    update_user,
)

users_bp = Blueprint("users", __name__, url_prefix="/api/v1/users")


@users_bp.get("")
@require_role(UserRole.ADMIN)
@rate_limit("admin")
def list_users_endpoint():
    try:
        users = list_users()
    except UserDatabaseError:
        return _error("database_error", 500)
    return jsonify({"success": True, "data": [_user_dict(user) for user in users]})


@users_bp.get("/<user_id>")
@require_role(UserRole.ADMIN)
@rate_limit("admin")
def get_user_endpoint(user_id: str):
    try:
        user = get_user(uuid.UUID(user_id))
    except (ValueError, AttributeError):
        return _error("validation_error", 400)
    except UserNotFoundError:
        return _error("user_not_found", 404)
    except UserDatabaseError:
        return _error("database_error", 500)
    return jsonify({"success": True, "data": _user_dict(user)})


@users_bp.post("")
@require_role(UserRole.ADMIN)
@rate_limit("admin")
def create_user_endpoint():
    body = request.get_json(silent=True)
    values, errors = _validate_create(body)
    if errors:
        return _error("validation_error", 400, errors)
    try:
        user = create_user(**values)
    except UserAlreadyExistsError:
        return _error("user_already_exists", 409)
    except UserDatabaseError:
        return _error("database_error", 500)
    return jsonify({"success": True, "data": _user_dict(user)}), 201


@users_bp.patch("/<user_id>")
@require_role(UserRole.ADMIN)
@rate_limit("admin")
def update_user_endpoint(user_id: str):
    try:
        parsed_id = uuid.UUID(user_id)
    except (ValueError, AttributeError):
        return _error("validation_error", 400)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not set(body).issubset({"email", "role", "is_active"}):
        return _error("validation_error", 400)
    role = None
    if "role" in body:
        try:
            role = UserRole(body["role"])
        except (ValueError, TypeError):
            return _error("validation_error", 400, {"role": "Role is invalid."})
    if "is_active" in body and not isinstance(body["is_active"], bool):
        return _error("validation_error", 400)
    try:
        user = update_user(parsed_id, email=body.get("email"), role=role, is_active=body.get("is_active"))
    except UserNotFoundError:
        return _error("user_not_found", 404)
    except UserAlreadyExistsError:
        return _error("user_already_exists", 409)
    except UserDatabaseError:
        return _error("database_error", 500)
    return jsonify({"success": True, "data": _user_dict(user)})


def _validate_create(body: Any):
    if not isinstance(body, dict):
        return {}, {"body": "Request body must be a JSON object."}
    errors = {}
    username, email, password, role_value = (body.get(key) for key in ("username", "email", "password", "role"))
    if not isinstance(username, str) or not username.strip() or len(username.strip()) > 128:
        errors["username"] = "Username is required and must be at most 128 characters."
    if not isinstance(email, str) or not email.strip() or len(email.strip()) > 320 or "@" not in email:
        errors["email"] = "A valid email is required."
    password_error = validate_password(password)
    if password_error:
        errors["password"] = password_error
    try:
        role = UserRole(role_value)
    except (ValueError, TypeError):
        errors["role"] = "Role must be ADMIN, OPERATOR, or VIEWER."
        role = None
    if errors:
        return {}, errors
    return {"username": username.strip(), "email": email.strip().lower(), "password": password, "role": role}, {}


def _user_dict(user):
    return {"id": str(user.id), "username": user.username, "email": user.email, "role": user.role.value, "is_active": user.is_active, "created_at": user.created_at.isoformat() if user.created_at else None, "updated_at": user.updated_at.isoformat() if user.updated_at else None, "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None}


def _error(code: str, status: int, details=None):
    error = {"code": code}
    if details:
        error["details"] = details
    return jsonify({"success": False, "error": error}), status
