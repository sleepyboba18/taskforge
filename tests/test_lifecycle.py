"""Regression tests for application lifecycle state transitions."""

from __future__ import annotations

import unittest

import app.lifecycle as lifecycle_module
from app import create_app
from app.lifecycle import LifecycleState


class LifecycleApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        lifecycle_module._lifecycle = None

    def test_create_app_marks_application_running(self) -> None:
        create_app()
        lifecycle = lifecycle_module.get_lifecycle()
        self.assertEqual(LifecycleState.RUNNING, lifecycle.state)
