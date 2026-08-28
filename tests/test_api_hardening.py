"""Production API boundary and error-contract tests."""

from __future__ import annotations

import unittest
from dataclasses import replace
import os
from unittest.mock import patch

from app import create_app
from app.config.settings import ConfigurationError, Settings


class ApiErrorBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app()
        cls.app.config["TASKFORGE_SETTINGS"] = replace(cls.app.config["TASKFORGE_SETTINGS"], rate_limit_enabled=False)
        cls.client = cls.app.test_client()

    def test_unknown_api_route_returns_json_request_id(self) -> None:
        response = self.client.get("/api/v1/does-not-exist")
        self.assertEqual(404, response.status_code)
        self.assertTrue(response.is_json)
        self.assertEqual("RESOURCE_NOT_FOUND", response.get_json()["error"]["code"])
        self.assertEqual(response.headers["X-Request-ID"], response.get_json()["request_id"])

    def test_unsupported_method_returns_json(self) -> None:
        response = self.client.put("/api/v1/health")
        self.assertEqual(405, response.status_code)
        self.assertTrue(response.is_json)
        self.assertEqual("METHOD_NOT_ALLOWED", response.get_json()["error"]["code"])

    def test_malformed_json_returns_json(self) -> None:
        response = self.client.post(
            "/api/v1/jobs",
            data='{"name":',
            content_type="application/json",
        )
        self.assertEqual(400, response.status_code)
        self.assertTrue(response.is_json)
        self.assertEqual("VALIDATION_ERROR", response.get_json()["error"]["code"])

    def test_oversized_body_is_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/jobs",
            data="x" * (2 * 1024 * 1024 + 1),
            content_type="application/json",
        )
        self.assertEqual(413, response.status_code)
        self.assertEqual("PAYLOAD_TOO_LARGE", response.get_json()["error"]["code"])

    def test_request_size_setting_is_validated(self) -> None:
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://user:pass@host/db", "MAX_REQUEST_BODY_MB": "0"}, clear=False):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment()
