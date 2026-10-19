"""Shared pytest configuration and opt-in PostgreSQL fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
import uuid
from urllib.parse import urlparse

import pytest
from dotenv import dotenv_values


_LOCAL_TEST_DATABASE_URL = (
    "postgresql+psycopg://taskforge_test:taskforge_test@127.0.0.1:5432/taskforge_test"
)
_TEST_JWT_SECRET = "taskforge-pytest-only-jwt-secret"
_TEST_FLASK_SECRET = "taskforge-pytest-only-secret"


def _database_target(database_url: str) -> tuple[str, str, int, str]:
    parsed = urlparse(database_url)
    default_port = 5432
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower(),
        parsed.port or default_port,
        parsed.path.rstrip("/"),
    )


def pytest_configure(config: pytest.Config) -> None:
    """Prevent app tests from inheriting deployment configuration."""
    configured_database_url = os.environ.get("DATABASE_URL", "").strip()
    dotenv_database_url = str(dotenv_values(".env").get("DATABASE_URL") or "").strip()
    test_database_url = os.environ.get("TASKFORGE_TEST_DATABASE_URL", "").strip()

    if test_database_url:
        parsed = urlparse(test_database_url)
        if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
            raise pytest.UsageError("TASKFORGE_TEST_DATABASE_URL must use PostgreSQL.")
        for candidate in (configured_database_url, dotenv_database_url):
            if candidate and _database_target(candidate) == _database_target(test_database_url):
                raise pytest.UsageError(
                    "TASKFORGE_TEST_DATABASE_URL must target a different PostgreSQL database."
                )
        selected_database_url = test_database_url
    else:
        selected_database_url = _LOCAL_TEST_DATABASE_URL

    os.environ.update(
        {
            "APP_ENV": "testing",
            "DATABASE_URL": selected_database_url,
            "DEBUG": "false",
            "JWT_SECRET_KEY": _TEST_JWT_SECRET,
            "SECRET_KEY": _TEST_FLASK_SECRET,
            "CORS_ORIGINS": "http://localhost",
            "CORS_SUPPORTS_CREDENTIALS": "false",
        }
    )


@pytest.fixture(scope="session")
def app() -> Iterator[object]:
    """Create an app on an explicitly selected, disposable PostgreSQL database."""
    database_url = os.environ.get("TASKFORGE_TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("set TASKFORGE_TEST_DATABASE_URL to enable PostgreSQL integration tests")

    from app import create_app
    from app.config.settings import Settings
    from app.database.session import Base, dispose_database, get_engine, initialize_schema

    settings = Settings.from_environment()
    dispose_database()
    try:
        application = create_app(settings)
        initialize_schema()
        yield application
    finally:
        try:
            Base.metadata.drop_all(get_engine())
        except RuntimeError:
            pass
        finally:
            dispose_database()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_app_database(request) -> Iterator[None]:
    """Isolate tests that request the PostgreSQL-backed app fixture."""
    if "app" not in request.fixturenames:
        yield
        return

    request.getfixturevalue("app")
    from sqlalchemy import text

    from app.database.session import Base, get_engine

    table_names = ", ".join(
        get_engine().dialect.identifier_preparer.quote(table.name)
        for table in Base.metadata.sorted_tables
    )

    def truncate_tables() -> None:
        with get_engine().begin() as connection:
            connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
        from app.database.session import initialize_schema

        initialize_schema()

    truncate_tables()
    yield
    truncate_tables()


@pytest.fixture
def db_session(app):
    """Yield an independent database session and always roll it back/close it."""
    from app.database.session import get_session_factory

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def user_factory():
    from app.models import User, UserRole

    def build_user(role: UserRole = UserRole.OPERATOR, **overrides) -> User:
        values = {
            "id": uuid.uuid4(),
            "username": f"test-{uuid.uuid4().hex[:12]}",
            "email": f"test-{uuid.uuid4().hex[:12]}@example.invalid",
            "password_hash": "not-a-real-password-hash",
            "role": role,
            "is_active": True,
        }
        values.update(overrides)
        return User(**values)

    return build_user


@pytest.fixture
def admin_user(user_factory):
    from app.models import UserRole

    return user_factory(role=UserRole.ADMIN)


@pytest.fixture
def regular_user(user_factory):
    from app.models import UserRole

    return user_factory(role=UserRole.OPERATOR)


@pytest.fixture
def job_factory():
    from app.models import Job, JobStatus

    def build_job(**overrides) -> Job:
        values = {
            "id": uuid.uuid4(),
            "name": "test-job",
            "task_type": "echo",
            "payload": {"message": "test"},
            "status": JobStatus.PENDING,
            "priority": 5,
            "max_retries": 2,
            "retry_count": 0,
        }
        values.update(overrides)
        return Job(**values)

    return build_job


@pytest.fixture
def worker_factory():
    from app.models import Worker, WorkerStatus

    def build_worker(**overrides) -> Worker:
        values = {
            "id": uuid.uuid4(),
            "worker_name": f"test-worker-{uuid.uuid4().hex[:12]}",
            "status": WorkerStatus.STARTING,
            "hostname": "test-host",
            "process_id": 1,
        }
        values.update(overrides)
        return Worker(**values)

    return build_worker