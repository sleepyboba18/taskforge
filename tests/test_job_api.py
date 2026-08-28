"""Focused API tests that do not require a local database."""

from __future__ import annotations

import unittest
import uuid
from dataclasses import replace
from datetime import timezone
from unittest.mock import patch

from app import create_app
from app.api.jobs import _validate_job_input
from app.models import Job, JobStatus, User, UserRole
from app.services.job_service import JobStateConflictError


class JobValidationTests(unittest.TestCase):
    def test_valid_scheduled_input_is_normalized_to_utc(self) -> None:
        values, errors = _validate_job_input(
            {
                "name": "send_email",
                "task_type": "email",
                "payload": {"recipient": "user@example.com"},
                "scheduled_at": "2026-09-01T15:00:00+05:30",
            }
        )
        self.assertEqual({}, errors)
        self.assertEqual(timezone.utc, values["scheduled_at"].tzinfo)
        self.assertEqual("2026-09-01T09:30:00+00:00", values["scheduled_at"].isoformat())

    def test_invalid_input_reports_field_errors(self) -> None:
        values, errors = _validate_job_input(
            {"name": "", "task_type": "email", "payload": [], "priority": -1, "max_retries": -1}
        )
        self.assertEqual({}, values)
        self.assertEqual({"name", "payload", "priority", "max_retries"}, set(errors))

    def test_naive_schedule_is_rejected(self) -> None:
        _, errors = _validate_job_input(
            {"name": "report", "task_type": "report", "scheduled_at": "2026-09-01T10:00:00"}
        )
        self.assertIn("scheduled_at", errors)


class JobRouteTests(unittest.TestCase):
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

    def test_creation_returns_safe_success_shape(self) -> None:
        job = Job(
            id=uuid.uuid4(),
            name="send_email",
            task_type="email",
            payload={"recipient": "user@example.com"},
            status=JobStatus.PENDING,
            priority=5,
            max_retries=3,
            retry_count=0,
        )
        with patch("app.api.jobs.create_job", return_value=job) as create:
            response = self.client.post(
                "/api/v1/jobs",
                json={"name": "send_email", "task_type": "email", "payload": {}},
            )
        self.assertEqual(201, response.status_code)
        self.assertTrue(response.get_json()["success"])
        self.assertEqual(str(job.id), response.get_json()["data"]["id"])
        create.assert_called_once()

    def test_validation_does_not_call_service(self) -> None:
        with patch("app.api.jobs.create_job") as create:
            response = self.client.post("/api/v1/jobs", json={"name": "missing task"})
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()["success"])
        create.assert_not_called()

    def test_cancel_conflict_is_returned_as_409(self) -> None:
        job_id = uuid.uuid4()
        with patch(
            "app.api.jobs.cancel_job",
            side_effect=JobStateConflictError(JobStatus.RUNNING),
        ):
            response = self.client.post(f"/api/v1/jobs/{job_id}/cancel")
        self.assertEqual(409, response.status_code)
        self.assertEqual("JOB_RUNNING", response.get_json()["error"]["code"])

    def test_invalid_list_query_is_returned_as_400(self) -> None:
        response = self.client.get("/api/v1/jobs?per_page=101")
        self.assertEqual(400, response.status_code)
        self.assertEqual("VALIDATION_ERROR", response.get_json()["error"]["code"])
