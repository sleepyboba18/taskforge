"""Worker engine tests that do not require a live PostgreSQL server."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.database.repositories.job_queue import ClaimedJob
from app.models import JobStatus
from app.services.task_executor import TaskRegistry, TaskValidationError, UnknownTaskError
from app.workers.worker import _job_event


class TaskRegistryTests(unittest.TestCase):
    def test_echo_is_explicitly_registered(self) -> None:
        payload = {"message": "hello"}
        self.assertEqual(payload, TaskRegistry.builtins().execute("echo", payload))

    def test_unknown_task_is_rejected(self) -> None:
        with self.assertRaises(UnknownTaskError):
            TaskRegistry.builtins().execute("os.system", {})

    def test_sleep_has_a_strict_bound(self) -> None:
        with self.assertRaises(TaskValidationError):
            TaskRegistry.builtins().execute("sleep", {"seconds": 11})

    @patch("app.services.task_executor.time.sleep")
    def test_sleep_uses_the_requested_safe_duration(self, sleep) -> None:
        result = TaskRegistry.builtins().execute("sleep", {"seconds": 0.1})
        sleep.assert_called_once_with(0.1)
        self.assertEqual({"slept": 0.1}, result)


class WorkerEventTests(unittest.TestCase):
    def test_job_event_contains_only_lifecycle_metadata(self) -> None:
        import uuid

        worker_id = uuid.uuid4()
        claimed = ClaimedJob(uuid.uuid4(), uuid.uuid4(), "echo", {"secret": "hidden"}, worker_id)
        event = _job_event(claimed, JobStatus.COMPLETED)
        self.assertEqual(
            {"id": str(claimed.job_id), "status": "COMPLETED", "worker_id": str(worker_id)},
            event,
        )
        self.assertNotIn("payload", event)
