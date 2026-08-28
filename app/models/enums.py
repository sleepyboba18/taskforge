"""Controlled database enum values."""

from enum import Enum


class JobStatus(str, Enum):
    """Lifecycle state of a submitted job."""

    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AttemptStatus(str, Enum):
    """Lifecycle state of one concrete execution attempt."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class WorkerStatus(str, Enum):
    """Lifecycle state of an independent worker process."""

    STARTING = "STARTING"
    IDLE = "IDLE"
    BUSY = "BUSY"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    UNHEALTHY = "UNHEALTHY"
    STALE = "STALE"
