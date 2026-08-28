"""JWT issuance and request-token validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from flask import current_app, g, request

from app.models import User

ALGORITHM = "HS256"


def issue_access_token(user: User) -> tuple[str, int]:
    settings = current_app.config["TASKFORGE_SETTINGS"]
    expires_in = settings.jwt_access_token_expires_minutes * 60
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user.id),
        "user_id": str(user.id),
        "role": user.role.value,
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
        claims: dict[str, Any] = jwt.decode(token, settings.jwt_secret_key, algorithms=[ALGORITHM])
        user_id = claims.get("sub")
        if not user_id:
            return None, "invalid_token"
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
