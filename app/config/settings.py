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
    misfire_policy: str
    worker_heartbeat_interval: float
    worker_stale_timeout: float
    recovery_poll_interval: float
    jwt_secret_key: str
    jwt_access_token_expires_minutes: int
    bootstrap_admin_username: str | None
    bootstrap_admin_email: str | None
    bootstrap_admin_password: str | None
    log_level: str
    slow_request_threshold_ms: int
    metrics_default_window: str
    rate_limit_enabled: bool
    rate_limit_requests: int
    rate_limit_window_seconds: int
    login_rate_limit_requests: int
    login_rate_limit_window_seconds: int
    rate_limit_admin: int
    rate_limit_operator: int
    rate_limit_viewer: int
    rate_limit_retention_seconds: int
    rate_limit_fail_open: bool
    max_job_dependencies: int
    max_dependency_graph_depth: int
    max_dependency_graph_nodes: int
    max_dependency_propagation_depth: int
    max_bulk_job_operations: int
    audit_retention_days: int
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
            misfire_policy=os.getenv("MISFIRE_POLICY", "SKIP").strip().upper(),
            worker_heartbeat_interval=_parse_positive_float("WORKER_HEARTBEAT_INTERVAL", default=5.0),
            worker_stale_timeout=_parse_positive_float("WORKER_STALE_TIMEOUT", default=30.0),
            recovery_poll_interval=_parse_positive_float("RECOVERY_POLL_INTERVAL", default=10.0),
            jwt_secret_key=os.getenv("JWT_SECRET_KEY", "").strip(),
            jwt_access_token_expires_minutes=_parse_bounded_int(
                "JWT_ACCESS_TOKEN_EXPIRES_MINUTES", default=60, maximum=1440
            ),
            bootstrap_admin_username=_optional_env("BOOTSTRAP_ADMIN_USERNAME"),
            bootstrap_admin_email=_optional_env("BOOTSTRAP_ADMIN_EMAIL"),
            bootstrap_admin_password=_optional_env("BOOTSTRAP_ADMIN_PASSWORD"),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
            slow_request_threshold_ms=_parse_bounded_int(
                "SLOW_REQUEST_THRESHOLD_MS", default=1000, maximum=600000
            ),
            metrics_default_window=os.getenv("METRICS_DEFAULT_WINDOW", "24h").strip().lower(),
            rate_limit_enabled=_parse_bool("RATE_LIMIT_ENABLED", default=True),
            rate_limit_requests=_parse_bounded_int("RATE_LIMIT_REQUESTS", default=60, maximum=100000),
            rate_limit_window_seconds=_parse_bounded_int("RATE_LIMIT_WINDOW_SECONDS", default=60, maximum=86400),
            login_rate_limit_requests=_parse_bounded_int("LOGIN_RATE_LIMIT_REQUESTS", default=10, maximum=10000),
            login_rate_limit_window_seconds=_parse_bounded_int("LOGIN_RATE_LIMIT_WINDOW_SECONDS", default=60, maximum=86400),
            rate_limit_admin=_parse_bounded_int("RATE_LIMIT_ADMIN", default=300, maximum=100000),
            rate_limit_operator=_parse_bounded_int("RATE_LIMIT_OPERATOR", default=120, maximum=100000),
            rate_limit_viewer=_parse_bounded_int("RATE_LIMIT_VIEWER", default=60, maximum=100000),
            rate_limit_retention_seconds=_parse_bounded_int("RATE_LIMIT_RETENTION_SECONDS", default=3600, maximum=604800),
            rate_limit_fail_open=_parse_bool("RATE_LIMIT_FAIL_OPEN", default=False),
            max_job_dependencies=_parse_bounded_int("MAX_JOB_DEPENDENCIES", default=100, maximum=10000),
            max_dependency_graph_depth=_parse_bounded_int("MAX_DEPENDENCY_GRAPH_DEPTH", default=50, maximum=1000),
            max_dependency_graph_nodes=_parse_bounded_int("MAX_DEPENDENCY_GRAPH_NODES", default=1000, maximum=100000),
            max_dependency_propagation_depth=_parse_bounded_int("MAX_DEPENDENCY_PROPAGATION_DEPTH", default=50, maximum=1000),
            max_bulk_job_operations=_parse_bounded_int("MAX_BULK_JOB_OPERATIONS", default=100, maximum=1000),
            audit_retention_days=_parse_non_negative_bounded_int("AUDIT_RETENTION_DAYS", default=0, maximum=3650),
            cors_origins=cors_origins,
            cors_supports_credentials=cors_origins != "*" and _parse_bool(
                "CORS_SUPPORTS_CREDENTIALS", default=False
            ),
        )
        if settings.retry_max_delay < settings.retry_base_delay:
            raise ConfigurationError("RETRY_MAX_DELAY must be greater than or equal to RETRY_BASE_DELAY.")
        if settings.misfire_policy != "SKIP":
            raise ConfigurationError("MISFIRE_POLICY currently supports only SKIP.")
        if settings.worker_stale_timeout <= settings.worker_heartbeat_interval:
            raise ConfigurationError("WORKER_STALE_TIMEOUT must be greater than WORKER_HEARTBEAT_INTERVAL.")
        if not settings.jwt_secret_key:
            raise ConfigurationError("JWT_SECRET_KEY is required and must not be empty.")
        bootstrap_values = (
            settings.bootstrap_admin_username,
            settings.bootstrap_admin_email,
            settings.bootstrap_admin_password,
        )
        if any(bootstrap_values) and not all(bootstrap_values):
            raise ConfigurationError("Bootstrap admin username, email, and password must be provided together.")
        if settings.bootstrap_admin_password and len(settings.bootstrap_admin_password) < 8:
            raise ConfigurationError("BOOTSTRAP_ADMIN_PASSWORD must be at least 8 characters.")
        if settings.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("LOG_LEVEL must be a standard logging level.")
        if settings.metrics_default_window not in {"1h", "24h", "7d"}:
            raise ConfigurationError("METRICS_DEFAULT_WINDOW must be 1h, 24h, or 7d.")
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


def _parse_non_negative_bounded_int(name: str, default: int, maximum: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a non-negative integer.") from exc
    if parsed < 0 or parsed > maximum:
        raise ConfigurationError(f"{name} must be between 0 and {maximum}.")
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


def _optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None
