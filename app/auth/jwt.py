"""JWT issuance and request-token validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from flask import current_app, g, request

from app.models import User, UserRole

ALGORITHM = "HS256"


def _validate_token_claims(claims: dict[str, Any]) -> str | None:
    """Validate the minimum claims required for a signed access token."""
    if not isinstance(claims, dict):
        return "invalid_token"
    if claims.get("token_type") not in {None, "access"}:
        return "invalid_token"
    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        return "invalid_token"
    if claims.get("user_id") != user_id:
        return "invalid_token"
    role_value = claims.get("role")
    try:
        UserRole(role_value)
    except (TypeError, ValueError):
        return "invalid_token"
    if "exp" not in claims or "iat" not in claims:
        return "invalid_token"
    return None


def issue_access_token(user: User) -> tuple[str, int]:
    settings = current_app.config["TASKFORGE_SETTINGS"]
    expires_in = settings.jwt_access_token_expires_minutes * 60
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user.id),
        "user_id": str(user.id),
        "role": user.role.value,
        "token_type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=ALGORITHM), expires_in


def authenticate_request() -> tuple[User | None, str | None]:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None, "authentication_required"
    try:
        settings = current_app.config["TASKFORGE_SETTINGS"]
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "user_id", "role", "iat", "exp", "token_type"]},
        )
        error = _validate_token_claims(claims)
        if error:
            return None, error
        user_id = claims["sub"]
        from app.database.session import session_scope
        with session_scope() as session:
            user = session.get(User, user_id)
            if user is None or not user.is_active:
                return None, "invalid_token"
            session.expunge(user)
            return user, None
    except jwt.ExpiredSignatureError:
        return None, "token_expired"
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None, "invalid_token"


def set_current_user(user: User) -> None:
    g.current_user = user
