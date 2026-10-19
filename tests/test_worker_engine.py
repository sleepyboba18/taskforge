"""Worker engine tests that do not require a live PostgreSQL server."""

from __future__ import annotations

import unittest
import inspect
from dataclasses import replace
from unittest.mock import MagicMock, patch

from app.database.repositories.job_queue import ClaimedJob
from app.config.settings import Settings
from app.models import JobStatus
from app.services.task_executor import TaskRegistry, TaskValidationError, UnknownTaskError
from app.workers.manager import WorkerManager
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


class WorkerManagerPoolTests(unittest.TestCase):
    def test_all_child_processes_receive_configured_pool_limits(self) -> None:
        settings = replace(
            Settings.from_environment(),
            worker_count=1,
            db_pool_size=3,
            db_max_overflow=1,
            db_pool_timeout=13,
            db_pool_recycle=700,
        )

        with patch("app.workers.manager.multiprocessing.Process") as process_factory:
            manager = WorkerManager(settings)
            manager.start()

        self.assertEqual(5, process_factory.call_count)
        for call in process_factory.call_args_list:
            with self.subTest(process=call.kwargs["name"]):
                child_kwargs = call.kwargs["kwargs"]
                self.assertEqual(3, child_kwargs["pool_size"])
                self.assertEqual(1, child_kwargs["max_overflow"])
                self.assertEqual(13, child_kwargs["pool_timeout"])
                self.assertEqual(700, child_kwargs["pool_recycle"])

    def test_child_process_kwargs_match_entrypoint_signatures(self) -> None:
        settings = replace(Settings.from_environment(), worker_count=1)
        with patch("app.workers.manager.multiprocessing.Process") as process_factory:
            WorkerManager(settings).start()

        for call in process_factory.call_args_list:
            target = call.kwargs["target"]
            with self.subTest(process=call.kwargs["name"]):
                inspect.signature(target).bind(**call.kwargs["kwargs"])

    def test_stop_bounds_total_join_time_by_shutdown_timeout(self) -> None:
        elapsed = 0

        def monotonic():
            return elapsed

        settings = replace(
            Settings.from_environment(),
            shutdown_timeout_seconds=10,
            worker_shutdown_timeout=6,
        )
        manager = WorkerManager(settings)
        manager._started = True
        manager._shutdown_event = MagicMock()
        manager._processes = [MagicMock() for _ in range(5)]
        for process in manager._processes:
            process.is_alive.return_value = False

            def join(timeout):
                nonlocal elapsed
                elapsed += timeout or 0

            process.join.side_effect = join

        with patch("app.workers.manager.time.monotonic", side_effect=monotonic):
            manager.stop()

        self.assertLessEqual(elapsed, settings.shutdown_timeout_seconds)
