"""Regression tests for application lifecycle state transitions."""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

import app.lifecycle as lifecycle_module
from app import create_app
from app.lifecycle import LifecycleState


class LifecycleApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        lifecycle_module._lifecycle = None

    def tearDown(self) -> None:
        # Reset lifecycle for other tests
        lifecycle_module._lifecycle = None

    def test_create_app_marks_application_running(self) -> None:
        create_app()
        lifecycle = lifecycle_module.get_lifecycle()
        self.assertEqual(LifecycleState.RUNNING, lifecycle.state)
        self.assertTrue(lifecycle.is_running)
        self.assertFalse(lifecycle.is_stopping)

    def test_lifecycle_state_transitions(self) -> None:
        lifecycle = lifecycle_module.get_lifecycle()
        self.assertEqual(LifecycleState.STARTING, lifecycle.state)
        
        lifecycle.mark_running()
        self.assertEqual(LifecycleState.RUNNING, lifecycle.state)
        self.assertTrue(lifecycle.is_running)
        
        lifecycle.mark_stopping()
        self.assertEqual(LifecycleState.STOPPING, lifecycle.state)
        self.assertTrue(lifecycle.is_stopping)
        self.assertTrue(lifecycle.shutdown_requested)
        
        lifecycle.mark_stopped()
        self.assertEqual(LifecycleState.STOPPED, lifecycle.state)
        self.assertTrue(lifecycle.is_stopping)

    def test_lifecycle_mark_running_idempotent(self) -> None:
        lifecycle = lifecycle_module.get_lifecycle()
        lifecycle.mark_running()
        self.assertEqual(LifecycleState.RUNNING, lifecycle.state)
        lifecycle.mark_running()
        self.assertEqual(LifecycleState.RUNNING, lifecycle.state)

    def test_lifecycle_repeated_shutdown_idempotent(self) -> None:
        lifecycle = lifecycle_module.get_lifecycle()
        lifecycle.mark_running()
        
        lifecycle.mark_stopping()
        self.assertTrue(lifecycle.shutdown_requested)
        
        lifecycle.mark_stopping()
        self.assertTrue(lifecycle.shutdown_requested)
        self.assertEqual(LifecycleState.STOPPING, lifecycle.state)

    def test_lifecycle_failure_during_startup(self) -> None:
        lifecycle = lifecycle_module.get_lifecycle()
        error = RuntimeError("Database connection failed")
        
        lifecycle.mark_failed(error)
        self.assertEqual(LifecycleState.FAILED, lifecycle.state)
        self.assertTrue(lifecycle.is_stopping)

    def test_lifecycle_failure_during_runtime(self) -> None:
        lifecycle = lifecycle_module.get_lifecycle()
        lifecycle.mark_running()
        
        error = RuntimeError("Unexpected error")
        lifecycle.mark_failed(error)
        self.assertEqual(LifecycleState.FAILED, lifecycle.state)

    def test_readiness_endpoint_rejects_during_shutdown(self) -> None:
        app = create_app()
        app.config["TASKFORGE_SETTINGS"] = replace(app.config["TASKFORGE_SETTINGS"], rate_limit_enabled=False)
        client = app.test_client()
        
        # Simulate shutdown state
        lifecycle_module.get_lifecycle().mark_stopping()
        
        response = client.get("/ready")
        self.assertEqual(503, response.status_code)
        self.assertEqual("not_ready", response.get_json()["status"])

    def test_job_submission_rejected_during_shutdown(self) -> None:
        from app.models import User, UserRole
        
        app = create_app()
        app.config["TASKFORGE_SETTINGS"] = replace(app.config["TASKFORGE_SETTINGS"], rate_limit_enabled=False)
        client = app.test_client()
        
        operator = User(
            id="test-op",
            username="operator",
            email="operator@example.com",
            password_hash="hash",
            role=UserRole.OPERATOR,
            is_active=True,
        )
        
        # Simulate shutdown state
        lifecycle_module.get_lifecycle().mark_stopping()
        
        with patch("app.auth.decorators.authenticate_request", return_value=(operator, None)):
            response = client.post("/api/v1/jobs", json={"queue": "default"})
        
        self.assertEqual(503, response.status_code)
        self.assertEqual("SERVICE_UNAVAILABLE", response.get_json()["error"]["code"])

    def test_lifecycle_to_dict_serialization(self) -> None:
        lifecycle = lifecycle_module.get_lifecycle()
        lifecycle.mark_running()
        
        data = lifecycle.to_dict()
        self.assertEqual(LifecycleState.RUNNING, data["state"])
        self.assertTrue(data["is_running"])
        self.assertFalse(data["is_stopping"])
        self.assertIn("uptime_seconds", data)
        self.assertGreaterEqual(data["uptime_seconds"], 0)
