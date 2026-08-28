"""Observability endpoint and request-context tests."""

from __future__ import annotations

import unittest
import uuid
from dataclasses import replace
from unittest.mock import patch

from app import create_app
from app.models import User, UserRole


class ObservabilityRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app()
        cls.app.config["TASKFORGE_SETTINGS"] = replace(cls.app.config["TASKFORGE_SETTINGS"], rate_limit_enabled=False)
        cls.client = cls.app.test_client()
        cls.viewer = User(
            id=uuid.uuid4(), username="viewer", email="viewer@example.com",
            password_hash="not-returned", role=UserRole.VIEWER, is_active=True,
        )

    def setUp(self) -> None:
        self.auth_patch = patch("app.auth.decorators.authenticate_request", return_value=(self.viewer, None))
        self.auth_patch.start()

    def tearDown(self) -> None:
        self.auth_patch.stop()

    def test_liveness_is_public_and_has_request_id(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.get_json()["status"], "ok")
        uuid.UUID(response.headers["X-Request-ID"])

    def test_supplied_request_id_is_reused(self) -> None:
        request_id = str(uuid.uuid4())
        response = self.client.get("/health", headers={"X-Request-ID": request_id})
        self.assertEqual(request_id, response.headers["X-Request-ID"])

    def test_readiness_returns_503_when_database_is_unavailable(self) -> None:
        with patch("app.api.observability.check_database_connection", side_effect=RuntimeError):
            response = self.client.get("/ready")
        self.assertEqual(503, response.status_code)
        self.assertEqual("not_ready", response.get_json()["status"])

    def test_metrics_requires_authentication(self) -> None:
        self.auth_patch.stop()
        response = self.client.get("/api/v1/metrics")
        self.assertEqual(401, response.status_code)

    def test_viewer_can_read_metrics_and_window_is_strict(self) -> None:
        with patch(
            "app.api.observability.collect_metrics",
            return_value={"window": "24h", "jobs": {}, "workers": {}, "performance": {}},
        ):
            response = self.client.get("/api/v1/metrics?window=24h")
        self.assertEqual(200, response.status_code)
        self.assertEqual("24h", response.get_json()["data"]["window"])
        self.assertTrue(response.get_json()["data"]["rate_limiting"]["enabled"] is False)

        response = self.client.get("/api/v1/metrics?window=30d")
        self.assertEqual(400, response.status_code)

    def test_viewer_can_read_detailed_health(self) -> None:
        with patch(
            "app.api.observability.detailed_health",
            return_value={"status": "healthy", "database": "healthy", "workers": "healthy"},
        ):
            response = self.client.get("/api/v1/health")
        self.assertEqual(200, response.status_code)
        self.assertEqual("healthy", response.get_json()["data"]["status"])

    def test_missing_database_metrics_are_controlled(self) -> None:
        with patch(
            "app.api.observability.collect_metrics",
            side_effect=RuntimeError("database internals"),
        ):
            response = self.client.get("/api/v1/metrics")
        self.assertEqual(500, response.status_code)
        self.assertNotIn("database internals", response.get_data(as_text=True))
