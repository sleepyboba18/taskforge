"""Environment-backed application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigurationError(RuntimeError):
    """Raised when required application configuration is invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated settings loaded from environment variables."""

    app_name: str
    app_env: str
    debug: bool
    host: str
    port: int
    database_url: str
    secret_key: str
    worker_count: int
    worker_poll_interval: float
    worker_shutdown_timeout: int
    retry_base_delay: float
    retry_max_delay: float
    retry_poll_interval: float
    retry_batch_size: int
    scheduler_poll_interval: float
    scheduler_batch_size: int
    cors_origins: str | list[str]
    cors_supports_credentials: bool

    @classmethod
    def from_environment(cls) -> "Settings":
        """Load `.env` and construct settings from process environment."""
        load_dotenv()
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise ConfigurationError(
                "DATABASE_URL is required. Set it in the environment or in .env."
            )

        cors_origins_value = os.getenv("CORS_ORIGINS", "*").strip() or "*"
        cors_origins: str | list[str]
        if cors_origins_value == "*":
            cors_origins = "*"
        else:
            cors_origins = [origin.strip() for origin in cors_origins_value.split(",") if origin.strip()]

        settings = cls(
            app_name=os.getenv("APP_NAME", "TaskForge"),
            app_env=os.getenv("APP_ENV", "development"),
            debug=_parse_bool("DEBUG", default=False),
            host=os.getenv("HOST", "127.0.0.1"),
            port=_parse_positive_int("PORT", default=5000),
            database_url=database_url,
            secret_key=os.getenv("SECRET_KEY", "change-me"),
            worker_count=_parse_bounded_int("WORKER_COUNT", default=2, maximum=32),
            worker_poll_interval=_parse_positive_float("WORKER_POLL_INTERVAL", default=1.0),
            worker_shutdown_timeout=_parse_bounded_int(
                "WORKER_SHUTDOWN_TIMEOUT", default=30, maximum=300
            ),
            retry_base_delay=_parse_non_negative_float("RETRY_BASE_DELAY", default=5.0),
            retry_max_delay=_parse_non_negative_float("RETRY_MAX_DELAY", default=3600.0),
            retry_poll_interval=_parse_positive_float("RETRY_POLL_INTERVAL", default=1.0),
            retry_batch_size=_parse_bounded_int("RETRY_BATCH_SIZE", default=100, maximum=1000),
            scheduler_poll_interval=_parse_positive_float("SCHEDULER_POLL_INTERVAL", default=1.0),
            scheduler_batch_size=_parse_bounded_int("SCHEDULER_BATCH_SIZE", default=100, maximum=1000),
            cors_origins=cors_origins,
            cors_supports_credentials=cors_origins != "*" and _parse_bool(
                "CORS_SUPPORTS_CREDENTIALS", default=False
            ),
        )
        if settings.retry_max_delay < settings.retry_base_delay:
            raise ConfigurationError("RETRY_MAX_DELAY must be greater than or equal to RETRY_BASE_DELAY.")
        return settings

    def as_flask_config(self) -> dict[str, object]:
        """Return only the settings Flask needs in its configuration mapping."""
        return {
            "APP_NAME": self.app_name,
            "APP_ENV": self.app_env,
            "DEBUG": self.debug,
            "SECRET_KEY": self.secret_key,
        }


def _parse_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean value.")


def _parse_positive_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a positive integer.") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be a positive integer.")
    return parsed


def _parse_bounded_int(name: str, default: int, maximum: int) -> int:
    parsed = _parse_positive_int(name, default)
    if parsed > maximum:
        raise ConfigurationError(f"{name} must be no greater than {maximum}.")
    return parsed


def _parse_positive_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a positive number.") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be a positive number.")
    return parsed


def _parse_non_negative_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a non-negative number.") from exc
    if parsed < 0:
        raise ConfigurationError(f"{name} must be a non-negative number.")
    return parsed
