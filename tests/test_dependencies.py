"""Focused dependency validation and readiness tests."""

from __future__ import annotations

import unittest
import uuid

from app.models import JobStatus
from app.services.dependency_service import (
    DependencyValidationError,
    dependency_state,
    validate_dependency_ids,
)


class DependencyValidationTests(unittest.TestCase):
    def test_missing_dependencies_are_backward_compatible(self) -> None:
        self.assertEqual([], validate_dependency_ids(None, maximum=100))
        self.assertEqual([], validate_dependency_ids([], maximum=100))

    def test_invalid_and_duplicate_dependencies_are_rejected(self) -> None:
        with self.assertRaises(DependencyValidationError):
            validate_dependency_ids("not-an-array", maximum=100)
        dependency_id = str(uuid.uuid4())
        with self.assertRaises(DependencyValidationError):
            validate_dependency_ids([dependency_id, dependency_id], maximum=100)

    def test_dependency_limit_is_enforced(self) -> None:
        with self.assertRaises(DependencyValidationError):
            validate_dependency_ids([str(uuid.uuid4()), str(uuid.uuid4())], maximum=1)


class DependencyStateTests(unittest.TestCase):
    def test_no_dependencies_are_ready(self) -> None:
        class EmptySession:
            def execute(self, statement):
                return type("Result", (), {"scalars": lambda self: iter(())})()

        from app.services.dependency_service import DependencyState

        self.assertEqual(DependencyState.READY, dependency_state(EmptySession(), uuid.uuid4()))

    def test_status_enum_keeps_dependency_states_out_of_job_statuses(self) -> None:
        self.assertNotIn("WAITING", {status.value for status in JobStatus})
        self.assertNotIn("BLOCKED", {status.value for status in JobStatus})