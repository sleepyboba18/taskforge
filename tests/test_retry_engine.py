"""Deterministic retry policy and configuration tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config.settings import ConfigurationError, Settings
from app.services.retry_policy import RetryPolicy
from app.services.task_executor import NonRetryableTaskError


class RetryPolicyTests(unittest.TestCase):
    def test_exponential_backoff_is_capped(self) -> None:
        policy = RetryPolicy(base_delay=5, max_delay=25)
        self.assertEqual([5, 10, 20, 25], [policy.delay_for(number) for number in range(1, 5)])

    def test_retry_count_represents_retries_after_initial_attempt(self) -> None:
        policy = RetryPolicy(base_delay=1, max_delay=10)
        decision = policy.decide(retry_count=0, max_retries=3, error=RuntimeError())
        self.assertTrue(decision.should_retry)
        self.assertEqual(1, decision.retry_number)
        self.assertEqual(1, decision.delay_seconds)
        self.assertFalse(
            policy.decide(retry_count=3, max_retries=3, error=RuntimeError()).should_retry
        )

    def test_non_retryable_error_bypasses_remaining_retries(self) -> None:
        decision = RetryPolicy().decide(
            retry_count=0, max_retries=3, error=NonRetryableTaskError()
        )
        self.assertFalse(decision.should_retry)

    def test_invalid_policy_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RetryPolicy(base_delay=10, max_delay=5)


class RetryConfigurationTests(unittest.TestCase):
    def test_max_delay_must_not_be_less_than_base_delay(self) -> None:
        environment = {
            "DATABASE_URL": "postgresql+psycopg://user:pass@host/db",
            "RETRY_BASE_DELAY": "10",
            "RETRY_MAX_DELAY": "5",
        }
        with patch.dict(os.environ, environment, clear=False):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment()
