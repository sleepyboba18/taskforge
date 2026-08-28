"""Centralized validation for Job lifecycle transitions."""

from __future__ import annotations

from app.models import JobStatus


VALID_TRANSITIONS = {
    JobStatus.PENDING: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.SCHEDULED: {JobStatus.PENDING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {JobStatus.COMPLETED, JobStatus.RETRYING, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.RETRYING: {JobStatus.PENDING, JobStatus.CANCELLED},
    JobStatus.FAILED: {JobStatus.PENDING},
    JobStatus.COMPLETED: set(),
    JobStatus.CANCELLED: set(),
}


class InvalidJobTransitionError(RuntimeError):
    """Raised when a Job state transition is not permitted."""


def validate_transition(current: JobStatus, target: JobStatus) -> None:
    if target not in VALID_TRANSITIONS.get(current, set()):
        raise InvalidJobTransitionError(f"Job cannot transition from {current.value} to {target.value}.")


def transition(job, target: JobStatus) -> None:
    validate_transition(job.status, target)
    job.status = target