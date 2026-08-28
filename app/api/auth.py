"""Authentication endpoints."""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import require_authentication
from app.auth.jwt import issue_access_token
from app.auth.password import validate_password
from app.rate_limit import rate_limit
from app.services.user_service import InvalidCredentialsError, UserDatabaseError, authenticate_user, change_password


auth_bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")


@auth_bp.post("/login")
@rate_limit("auth")
def login():
    body = request.get_json(silent=True)
    if isinstance(body, dict) and set(body) - {"username", "password"}:
        return _error("validation_error", 400)
    if not isinstance(body, dict) or not isinstance(body.get("username"), str) or not isinstance(body.get("password"), str):
        return _error("validation_error", 400)
    try:
        user = authenticate_user(body["username"].strip(), body["password"])
        token, expires_in = issue_access_token(user)
    except InvalidCredentialsError:
        return _error("invalid_credentials", 401)
    except UserDatabaseError:
        return _error("database_error", 500)
    return jsonify({"success": True, "data": {"access_token": token, "token_type": "Bearer", "expires_in": expires_in}})


@auth_bp.get("/me")
@require_authentication
@rate_limit("read")
def me():
    user = g.current_user
    return jsonify({"success": True, "data": _user_dict(user)})


@auth_bp.post("/change-password")
@require_authentication
@rate_limit("write")
def change_own_password():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - {"current_password", "new_password"}:
        return _error("validation_error", 400)
    current = body.get("current_password")
    new = body.get("new_password")
    validation = validate_password(new)
    if not isinstance(current, str) or validation:
        return _error("validation_error", 400, {"new_password": validation or "Current password is required."})
    try:
        change_password(g.current_user.id, current, new)
    except InvalidCredentialsError:
        return _error("invalid_credentials", 401)
    except UserDatabaseError:
        return _error("database_error", 500)
    return jsonify({"success": True, "message": "Password changed successfully."})


def _user_dict(user):
    return {"id": str(user.id), "username": user.username, "email": user.email, "role": user.role.value, "is_active": user.is_active}


def _error(code: str, status: int, details=None):
    error = {"code": code}
    if details:
        error["details"] = details
    return jsonify({"success": False, "error": error}), status
