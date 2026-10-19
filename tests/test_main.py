"""Application entry-point startup and cleanup tests."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import main


class MainLifecycleTests(unittest.TestCase):
    def test_normal_entry_point_starts_and_cleans_up(self) -> None:
        settings = MagicMock()
        settings.bootstrap_admin_username = None
        settings.app_name = "TaskForge-Test"
        settings.host = "127.0.0.1"
        settings.port = 5000
        settings.debug = False
        manager = MagicMock()
        lifecycle = MagicMock()

        with (
            patch.object(main.Settings, "from_environment", return_value=settings),
            patch.object(main, "create_app", return_value=object()),
            patch.object(main, "WorkerManager", return_value=manager),
            patch.object(main, "get_lifecycle", return_value=lifecycle),
            patch.object(main, "install_shutdown_handlers"),
            patch.object(main.socketio, "run") as run_server,
            patch.object(main, "broadcast_server_shutdown"),
            patch.object(main, "dispose_database"),
        ):
            main.main()

        manager.start.assert_called_once_with()
        manager.stop.assert_called_once_with()
        run_server.assert_called_once()
        lifecycle.mark_stopping.assert_called_once_with()
        lifecycle.mark_stopped.assert_called_once_with()

    def test_worker_start_failure_runs_centralized_cleanup(self) -> None:
        settings = MagicMock()
        settings.bootstrap_admin_username = None
        manager = MagicMock()
        manager.start.side_effect = RuntimeError("controlled startup failure")
        lifecycle = MagicMock()

        with (
            patch.object(main.Settings, "from_environment", return_value=settings),
            patch.object(main, "create_app", return_value=object()),
            patch.object(main, "WorkerManager", return_value=manager),
            patch.object(main, "get_lifecycle", return_value=lifecycle),
            patch.object(main, "install_shutdown_handlers"),
            patch.object(main, "broadcast_server_shutdown") as broadcast,
            patch.object(main, "dispose_database") as dispose,
            self.assertRaisesRegex(RuntimeError, "controlled startup failure"),
        ):
            main.main()

        manager.stop.assert_called_once_with()
        lifecycle.mark_stopping.assert_called_once_with()
        lifecycle.mark_stopped.assert_called_once_with()
        broadcast.assert_called_once_with()
        dispose.assert_called_once_with()