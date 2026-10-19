"""Regression checks for pytest's isolated environment."""

from __future__ import annotations

import os
from urllib.parse import urlparse


def test_pytest_uses_isolated_application_configuration() -> None:
    database_url = os.environ["DATABASE_URL"]

    assert os.environ["APP_ENV"] == "testing"
    assert urlparse(database_url).scheme in {"postgresql", "postgresql+psycopg"}
    assert os.environ["JWT_SECRET_KEY"] == "taskforge-pytest-only-jwt-secret"
    assert os.environ["SECRET_KEY"] == "taskforge-pytest-only-secret"

    if os.environ.get("TASKFORGE_TEST_DATABASE_URL"):
        assert database_url == os.environ["TASKFORGE_TEST_DATABASE_URL"]
    else:
        assert urlparse(database_url).hostname == "127.0.0.1"
        assert urlparse(database_url).path == "/taskforge_test"