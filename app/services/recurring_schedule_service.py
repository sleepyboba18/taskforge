"""Cron and timezone calculations for recurring definitions."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ScheduleValidationError(ValueError):
    """Raised when a cron expression or timezone is invalid."""


def get_timezone(name: str) -> ZoneInfo:
    """Return a valid IANA timezone."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleValidationError("Timezone must be a valid IANA timezone.") from exc


def validate_schedule(expression: str) -> None:
    """Validate a standard five-field cron expression."""
    croniter = _croniter_class()
    try:
        if len(expression.split()) != 5:
            raise ValueError
        croniter(expression, datetime.now(timezone.utc))
    except (TypeError, ValueError) as exc:
        raise ScheduleValidationError("Schedule must be a valid five-field cron expression.") from exc


def next_occurrence(expression: str, timezone_name: str, after_utc: datetime) -> datetime:
    """Calculate the next local cron occurrence and return it as UTC."""
    validate_schedule(expression)
    local_zone = get_timezone(timezone_name)
    local_after = after_utc.astimezone(local_zone)
    occurrence = _croniter_class()(expression, local_after).get_next(datetime)
    if occurrence.tzinfo is None:
        occurrence = occurrence.replace(tzinfo=local_zone)
    return occurrence.astimezone(timezone.utc)


def normalize_utc(value: datetime) -> datetime:
    """Require an aware datetime and normalize it to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleValidationError("Schedule times must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _croniter_class():
    try:
        from croniter import croniter
    except ImportError as exc:
        raise RuntimeError("croniter is required for recurring schedules. Install requirements.txt.") from exc
    return croniter
