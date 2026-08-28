"""Rate-limit policy and decorator tests."""

from __future__ import annotations

import os
import unittest
import uuid
from dataclasses import replace
from unittest.mock import patch

from flask import Flask

from app.config.settings import ConfigurationError, Settings
from app.rate_limit.decorators import rate_limit
from app.rate_limit.service import RateLimitDecision, RateLimitUnavailableError, identity_key, window_start


class RateLimitServiceTests(unittest.TestCase):
    def test_window_start_is_utc_and_aligned(self) -> None:
        from datetime import datetime, timezone

        start = window_start(datetime(2026, 8, 28, 10, 2, 3, tzinfo=timezone.utc), 60)
        self.assertEqual(datetime(2026, 8, 28, 10, 2, tzinfo=timezone.utc), start)

    def test_authenticated_users_have_independent_endpoint_keys(self) -> None:
        first = identity_key(user_id=uuid.uuid4(), client_ip="127.0.0.1", endpoint="GET:/jobs")
        second = identity_key(user_id=uuid.uuid4(), client_ip="127.0.0.1", endpoint="GET:/jobs")
        self.assertNotEqual(first, second)

    def test_invalid_rate_configuration_is_rejected(self) -> None:
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://user:pass@host/db", "RATE_LIMIT_REQUESTS": "-1"}, clear=False):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment()


class RateLimitDecoratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        settings = Settings.from_environment()
        self.app.config["TASKFORGE_SETTINGS"] = replace(settings, rate_limit_enabled=True)

        @self.app.get("/limited")
        @rate_limit("read")
        def limited():
            return {"success": True}

        self.client = self.app.test_client()

    def test_allowed_response_has_rate_headers(self) -> None:
        decision = RateLimitDecision(True, 3, 2, 1_800_000_000, 0)
        with patch("app.rate_limit.decorators.consume", return_value=decision):
            response = self.client.get("/limited")
        self.assertEqual(200, response.status_code)
        self.assertEqual("3", response.headers["X-RateLimit-Limit"])
        self.assertEqual("2", response.headers["X-RateLimit-Remaining"])

    def test_exceeded_response_has_retry_after(self) -> None:
        decision = RateLimitDecision(False, 3, 0, 1_800_000_010, 10)
        with patch("app.rate_limit.decorators.consume", return_value=decision):
            response = self.client.get("/limited")
        self.assertEqual(429, response.status_code)
        self.assertEqual("10", response.headers["Retry-After"])
        self.assertEqual("rate_limit_exceeded", response.get_json()["error"]["code"])

    def test_database_failure_is_controlled(self) -> None:
        with patch(
            "app.rate_limit.decorators.consume",
            side_effect=RateLimitUnavailableError,
        ):
            response = self.client.get("/limited")
        self.assertEqual(503, response.status_code)
        self.assertEqual("rate_limit_unavailable", response.get_json()["error"]["code"])
