"""Atomic PostgreSQL fixed-window rate-limit operations."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.database.session import session_scope
from app.models import RateLimitRecord, UserRole

logger = logging.getLogger("taskforge.rate_limit")


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Admission result and response-header metadata."""

    allowed: bool
    limit: int
    remaining: int
    reset_at: int
    retry_after: int


class RateLimitUnavailableError(RuntimeError):
    """Raised when the authoritative rate-limit store cannot be reached."""


def window_start(now: datetime, window_seconds: int) -> datetime:
    """Return the UTC fixed-window boundary containing now."""
    now_utc = now.astimezone(timezone.utc)
    epoch = int(now_utc.timestamp())
    return datetime.fromtimestamp(epoch - epoch % window_seconds, timezone.utc)


def identity_key(*, user_id: uuid.UUID | None, client_ip: str, endpoint: str) -> str:
    """Build a stable identity/endpoint key without trusting proxy headers."""
    identity = f"user:{user_id}" if user_id is not None else f"ip:{client_ip}"
    return f"{identity}:{endpoint}"


def consume(*, key: str, limit: int, window_seconds: int, fail_open: bool) -> RateLimitDecision:
    """Atomically increment a shared counter and decide admission."""
    now = datetime.now(timezone.utc)
    start = window_start(now, window_seconds)
    reset_at = int((start + timedelta(seconds=window_seconds)).timestamp())
    try:
        with session_scope() as session:
            statement = insert(RateLimitRecord).values(
                id=uuid.uuid4(), key=key, window_start=start, request_count=1
            ).on_conflict_do_update(
                index_elements=[RateLimitRecord.key, RateLimitRecord.window_start],
                set_={
                    "request_count": RateLimitRecord.request_count + 1,
                    "updated_at": func.now(),
                },
            ).returning(RateLimitRecord.request_count)
            count = session.scalar(statement)
            session.commit()
    except SQLAlchemyError as exc:
        logger.exception("Rate-limit database operation failed")
        if fail_open:
            return RateLimitDecision(True, limit, limit, reset_at, 0)
        raise RateLimitUnavailableError from exc

    count = int(count or 1)
    remaining = max(0, limit - count)
    retry_after = max(0, reset_at - int(now.timestamp()))
    return RateLimitDecision(count <= limit, limit, remaining, reset_at, retry_after)


def cleanup(*, retention_seconds: int) -> int:
    """Delete expired rate-limit windows in one bounded database statement."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=retention_seconds)
    try:
        with session_scope() as session:
            result = session.execute(delete(RateLimitRecord).where(RateLimitRecord.window_start < cutoff))
            session.commit()
            return result.rowcount or 0
    except SQLAlchemyError:
        logger.exception("Rate-limit cleanup failed")
        return 0


def role_limit(role: UserRole | None, default: int, admin: int, operator: int, viewer: int) -> int:
    return {
        UserRole.ADMIN: admin,
        UserRole.OPERATOR: operator,
        UserRole.VIEWER: viewer,
    }.get(role, default)
