"""Deterministic concurrency invariants that do not require PostgreSQL."""

from __future__ import annotations

import unittest

from app.models import JobStatus
from app.services.job_state_machine import InvalidJobTransitionError, validate_transition


class JobStateMachineTests(unittest.TestCase):
    def test_terminal_jobs_cannot_be_reexecuted(self) -> None:
        with self.assertRaises(InvalidJobTransitionError):
            validate_transition(JobStatus.COMPLETED, JobStatus.RUNNING)
        with self.assertRaises(InvalidJobTransitionError):
            validate_transition(JobStatus.CANCELLED, JobStatus.RUNNING)

    def test_claim_and_retry_transitions_are_explicit(self) -> None:
        validate_transition(JobStatus.PENDING, JobStatus.RUNNING)
        validate_transition(JobStatus.RUNNING, JobStatus.RETRYING)
        validate_transition(JobStatus.RETRYING, JobStatus.PENDING)