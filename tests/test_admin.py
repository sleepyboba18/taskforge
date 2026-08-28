"""Focused administrative control contract tests."""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from app import create_app
from app.models import User, UserRole


class AdminRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app()
        cls.app.config["TASKFORGE_SETTINGS"] = replace(cls.app.config["TASKFORGE_SETTINGS"], rate_limit_enabled=False)
        cls.client = cls.app.test_client()
        cls.operator = User(role=UserRole.OPERATOR)

    def setUp(self) -> None:
        self.auth = patch("app.auth.decorators.authenticate_request", return_value=(self.operator, None))
        self.auth.start()

    def tearDown(self) -> None:
        self.auth.stop()

    def test_bulk_limit_is_enforced(self) -> None:
        response = self.client.post("/api/v1/admin/jobs/cancel", json={"job_ids": ["bad"] * 101})
        self.assertEqual(400, response.status_code)

    def test_queue_pause_is_idempotent_at_service_boundary(self) -> None:
        with patch("app.api.admin.set_queue_paused", return_value={"paused": True, "result": "already_paused"}):
            response = self.client.post("/api/v1/admin/queue/pause")
        self.assertEqual(200, response.status_code)
        self.assertEqual("already_paused", response.get_json()["data"]["result"])

    def test_reason_rejects_control_characters(self) -> None:
        with patch("app.api.admin.cancel_job") as cancel:
            response = self.client.post("/api/v1/admin/jobs/00000000-0000-0000-0000-000000000000/cancel", json={"reason": "bad\nreason"})
        self.assertEqual(400, response.status_code)
        cancel.assert_not_called()