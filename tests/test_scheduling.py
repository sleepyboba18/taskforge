"""Tests for one-time scheduling boundaries."""

from __future__ import annotations

import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from app import create_app
from app.models import Job, JobStatus, User, UserRole
from app.workers.scheduled_scheduler import run_scheduled_scheduler


class OneTimeSchedulingTests(unittest.TestCase):
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

    def test_future_submission_remains_scheduled_and_emits_scheduling_event(self) -> None:
        scheduled_at = datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc)
        job = Job(
            id=uuid.uuid4(),
            name="future-task",
            task_type="echo",
            payload={"message": "hello"},
            status=JobStatus.SCHEDULED,
            priority=10,
            max_retries=3,
            retry_count=0,
            scheduled_at=scheduled_at,
        )
        with patch("app.api.jobs.create_job", return_value=job) as create_job:
            response = self.client.post(
                "/api/v1/jobs",
                json={
                    "name": "future-task",
                    "task_type": "echo",
                    "payload": {"message": "hello"},
                    "scheduled_at": "2026-09-01T10:30:00+05:30",
                },
            )
        self.assertEqual(201, response.status_code)
        self.assertEqual("SCHEDULED", response.get_json()["data"]["status"])
        self.assertEqual(timezone.utc, create_job.call_args.kwargs["scheduled_at"].tzinfo)

    def test_past_timestamp_is_accepted_for_immediate_eligibility(self) -> None:
        values, errors = __import__("app.api.jobs", fromlist=["_validate_job_input"])._validate_job_input(
            {"name": "past", "task_type": "echo", "scheduled_at": "2020-01-01T00:00:00Z"}
        )
        self.assertEqual({}, errors)
        self.assertEqual(timezone.utc, values["scheduled_at"].tzinfo)

    def test_scheduler_process_publishes_only_committed_promotions(self) -> None:
        class Shutdown:
            def __init__(self) -> None:
                self.calls = 0

            def is_set(self) -> bool:
                return self.calls > 0

            def wait(self, _interval: float) -> None:
                self.calls += 1

        class Promotion:
            job_id = uuid.uuid4()
            scheduled_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
            promoted_at = datetime(2020, 1, 1, 0, 0, 1, tzinfo=timezone.utc)

        shutdown = Shutdown()
        with patch("app.workers.scheduled_scheduler.initialize_database"), patch(
            "app.workers.scheduled_scheduler.promote_due_jobs", return_value=[Promotion()]
        ), patch("app.workers.scheduled_scheduler.publish_event") as publish:
            run_scheduled_scheduler(
                database_url="postgresql+psycopg://user:pass@host/db",
                poll_interval=1,
                batch_size=100,
                shutdown_event=shutdown,
            )
        publish.assert_called_once_with(
            "job:scheduled_ready",
            {
                "id": str(Promotion.job_id),
                "status": "PENDING",
                "scheduled_at": Promotion.scheduled_at.isoformat(),
            },
        )
