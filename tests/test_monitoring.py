"""Focused monitoring calculations and configuration tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config.settings import ConfigurationError, Settings
from app.services.monitoring_service import _alerts, _rate, validate_window


class MonitoringCalculationTests(unittest.TestCase):
    def test_supported_windows_are_explicit(self) -> None:
        for window in ("1m", "5m", "15m", "1h", "6h", "24h", "7d"):
            self.assertEqual(window, validate_window(window))
        with self.assertRaises(ValueError):
            validate_window("1month")

    def test_empty_rates_are_zero(self) -> None:
        self.assertEqual(0.0, _rate(0, 0))
        self.assertEqual(25.0, _rate(1, 4))

    def test_alerts_detect_starvation_and_stale_workers(self) -> None:
        settings = type("Settings", (), {
            "queue_backlog_warning_threshold": 100,
            "dlq_backlog_warning_threshold": 50,
            "worker_saturation_threshold_percent": 90,
        })()
        alerts = _alerts(
            queue_depth=2, pending=2, active_workers=0, stale_workers=1,
            utilization=0, dlq_depth=0, failed_window=0, previous_failed=0,
            retry_window=0, previous_retry=0, settings=settings,
        )
        codes = {alert["code"] for alert in alerts}
        self.assertIn("QUEUE_STARVATION", codes)
        self.assertIn("STALE_WORKERS_PRESENT", codes)


class MonitoringConfigurationTests(unittest.TestCase):
    def test_negative_threshold_is_rejected(self) -> None:
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg://user:pass@host/db", "QUEUE_BACKLOG_WARNING_THRESHOLD": "-1"}, clear=False):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment()
