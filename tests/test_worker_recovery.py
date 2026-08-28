"""Worker heartbeat and recovery boundary tests."""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from app import create_app
from app.config.settings import ConfigurationError, Settings
from app.models import User, UserRole, Worker, WorkerStatus
from app.services.worker_service import worker_health
from app.workers.worker import _job_event


class WorkerHealthApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app()
        cls.client = cls.app.test_client()
        cls.user = User(id=uuid.uuid4(), username="viewer", email="viewer@example.com", password_hash="hash", role=UserRole.VIEWER, is_active=True)

    def setUp(self):
        self.auth_patch = patch("app.auth.decorators.authenticate_request", return_value=(self.user, None))
        self.auth_patch.start()

    def tearDown(self):
        self.auth_patch.stop()

    def test_malformed_worker_id_is_rejected(self) -> None:
        response = self.client.get("/api/v1/workers/not-a-uuid")
        self.assertEqual(400, response.status_code)
        self.assertEqual("VALIDATION_ERROR", response.get_json()["error"]["code"])

    def test_invalid_worker_status_is_rejected(self) -> None:
        response = self.client.get("/api/v1/workers?status=UNKNOWN")
        self.assertEqual(400, response.status_code)

    def test_worker_detail_is_serialized(self) -> None:
        worker = Worker(
            id=uuid.uuid4(), worker_name="worker-instance", hostname="host", process_id=123,
            status=WorkerStatus.STALE,
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_heartbeat_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        with patch("app.api.workers.get_worker", return_value=worker):
            response = self.client.get(f"/api/v1/workers/{worker.id}")
        self.assertEqual(200, response.status_code)
        self.assertEqual("STALE", response.get_json()["data"]["status"])


class WorkerConfigurationTests(unittest.TestCase):
    def test_stale_timeout_must_exceed_heartbeat_interval(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+psycopg://user:pass@host/db",
                "WORKER_HEARTBEAT_INTERVAL": "5",
                "WORKER_STALE_TIMEOUT": "5",
            },
            clear=False,
        ):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment()


class WorkerEventTests(unittest.TestCase):
    def test_health_summary_uses_persistent_worker_query(self) -> None:
        with patch("app.services.worker_service.session_scope") as scope:
            scope.side_effect = RuntimeError("database boundary reached")
            with self.assertRaises(RuntimeError):
                worker_health(stale_timeout=30)
