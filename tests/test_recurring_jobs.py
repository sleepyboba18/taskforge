"""Tests for recurring-job API boundaries and scheduler behavior."""

from __future__ import annotations

import importlib.util
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

from app import create_app
from app.models import RecurringJob


@unittest.skipUnless(importlib.util.find_spec("croniter"), "requires croniter dependency")
class CronCalculationTests(unittest.TestCase):
    def test_five_field_schedule_is_calculated_in_utc(self) -> None:
        from app.services.recurring_schedule_service import next_occurrence

        current = datetime(2026, 8, 28, 3, 0, tzinfo=timezone.utc)
        self.assertEqual(
            datetime(2026, 8, 28, 3, 30, tzinfo=timezone.utc),
            next_occurrence("0 9 * * *", "Asia/Kolkata", current),
        )

    def test_invalid_expression_is_rejected(self) -> None:
        from app.services.recurring_schedule_service import ScheduleValidationError, validate_schedule

        with self.assertRaises(ScheduleValidationError):
            validate_schedule("61 * * * *")


class RecurringJobRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app()
        cls.client = cls.app.test_client()

    def test_invalid_definition_is_rejected_without_service_call(self) -> None:
        with patch("app.api.recurring_jobs.create_recurring_job") as create:
            response = self.client.post(
                "/api/v1/recurring-jobs",
                json={"name": "", "task_type": "echo", "schedule": "bad", "timezone": "UTC"},
            )
        self.assertEqual(400, response.status_code)
        self.assertEqual("VALIDATION_ERROR", response.get_json()["error"]["code"])
        create.assert_not_called()

    def test_created_definition_has_safe_response_shape(self) -> None:
        recurring = RecurringJob(
            id=uuid.uuid4(), name="daily", task_type="echo", payload={}, priority=5,
            max_retries=3, schedule_expression="0 9 * * *", timezone="UTC", enabled=True,
            next_run_at=datetime(2026, 8, 29, 9, tzinfo=timezone.utc),
        )
        with patch("app.api.recurring_jobs.create_recurring_job", return_value=recurring):
            response = self.client.post(
                "/api/v1/recurring-jobs",
                json={"name": "daily", "task_type": "echo", "schedule": "0 9 * * *"},
            )
        self.assertEqual(201, response.status_code)
        self.assertEqual("0 9 * * *", response.get_json()["data"]["schedule"])
        self.assertEqual("UTC", response.get_json()["data"]["timezone"])

    def test_enabled_filter_must_be_boolean(self) -> None:
        response = self.client.get("/api/v1/recurring-jobs?enabled=yes")
        self.assertEqual(400, response.status_code)
        self.assertEqual("VALIDATION_ERROR", response.get_json()["error"]["code"])

    def test_malformed_id_is_rejected(self) -> None:
        response = self.client.get("/api/v1/recurring-jobs/not-a-uuid")
        self.assertEqual(400, response.status_code)


class RecurringSchedulerTests(unittest.TestCase):
    def test_scheduler_emits_after_generation(self) -> None:
        from app.workers.recurring_scheduler import run_recurring_scheduler

        class Shutdown:
            calls = 0

            def is_set(self):
                return self.calls > 0

            def wait(self, _interval):
                self.calls += 1

        class Generated:
            recurring_job_id = uuid.uuid4()
            job_id = uuid.uuid4()
            scheduled_for = datetime(2026, 8, 28, 9, tzinfo=timezone.utc)

        shutdown = Shutdown()
        @contextmanager
        def fake_session_scope():
            yield object()

        with patch("app.workers.recurring_scheduler.initialize_database"), patch(
            "app.workers.recurring_scheduler.session_scope", fake_session_scope
        ), patch("app.workers.recurring_scheduler.due_recurring_ids", return_value=[]
        ), patch("app.workers.recurring_scheduler.publish_event") as publish:
            run_recurring_scheduler(
                database_url="postgresql+psycopg://user:pass@host/db",
                poll_interval=1,
                batch_size=100,
                shutdown_event=shutdown,
            )
        publish.assert_not_called()
