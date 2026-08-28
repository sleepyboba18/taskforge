"""Dead-letter API and failure-outcome tests."""

from __future__ import annotations

import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from app import create_app
from app.models import DeadLetterJob, Job, JobStatus, User, UserRole
from app.database.repositories.retry_repository import RetryOutcome


class DeadLetterRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app()
        cls.app.config["TASKFORGE_SETTINGS"] = replace(cls.app.config["TASKFORGE_SETTINGS"], rate_limit_enabled=False)
        cls.client = cls.app.test_client()
        cls.user = User(id=uuid.uuid4(), username="operator", email="operator@example.com", password_hash="hash", role=UserRole.OPERATOR, is_active=True)

    def setUp(self):
        self.auth_patch = patch("app.auth.decorators.authenticate_request", return_value=(self.user, None))
        self.auth_patch.start()

    def tearDown(self):
        self.auth_patch.stop()

    def test_list_has_bounded_pagination(self) -> None:
        response = self.client.get("/api/v1/dead-letters?per_page=101")
        self.assertEqual(400, response.status_code)
        self.assertEqual("VALIDATION_ERROR", response.get_json()["error"]["code"])

    def test_malformed_id_is_rejected(self) -> None:
        response = self.client.get("/api/v1/dead-letters/not-a-uuid")
        self.assertEqual(400, response.status_code)

    def test_get_returns_serialized_record(self) -> None:
        record = DeadLetterJob(
            id=uuid.uuid4(), job_id=uuid.uuid4(), task_type="echo", payload={"x": 1},
            error_type="RuntimeError", error_message="task execution failed", attempt_count=2,
            failed_at=datetime.now(timezone.utc), source="TASK_EXECUTION",
        )
        with patch("app.api.dead_letters.get_dead_letter_by_id", return_value=record):
            response = self.client.get(f"/api/v1/dead-letters/{record.id}")
        self.assertEqual(200, response.status_code)
        self.assertEqual(str(record.job_id), response.get_json()["data"]["job_id"])

    def test_retry_returns_requeued_job(self) -> None:
        job = Job(
            id=uuid.uuid4(), name="failed", task_type="echo", payload={}, status=JobStatus.PENDING,
            priority=5, max_retries=1, retry_count=0,
        )
        dead_letter_id = uuid.uuid4()
        with patch("app.api.dead_letters.retry_dead_letter", return_value=job):
            response = self.client.post(f"/api/v1/dead-letters/{dead_letter_id}/retry")
        self.assertEqual(200, response.status_code)
        self.assertEqual("PENDING", response.get_json()["data"]["status"])


class RetryOutcomeTests(unittest.TestCase):
    def test_retryable_outcome_has_no_dead_letter(self) -> None:
        outcome = RetryOutcome(
            job_id=uuid.uuid4(), attempt_id=uuid.uuid4(), worker_id=uuid.uuid4(),
            status=JobStatus.RETRYING, retry_count=1, max_retries=2,
            next_retry_at=datetime.now(timezone.utc), attempt_number=1,
        )
        self.assertIsNone(outcome.dead_letter_id)

    def test_terminal_outcome_can_reference_dead_letter(self) -> None:
        dead_letter_id = uuid.uuid4()
        outcome = RetryOutcome(
            job_id=uuid.uuid4(), attempt_id=uuid.uuid4(), worker_id=uuid.uuid4(),
            status=JobStatus.FAILED, retry_count=1, max_retries=1,
            next_retry_at=None, attempt_number=2, dead_letter_id=dead_letter_id,
        )
        self.assertEqual(dead_letter_id, outcome.dead_letter_id)
