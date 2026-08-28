"""Concurrent PostgreSQL queue worker infrastructure."""

__all__ = ["WorkerManager", "run_worker"]


def __getattr__(name: str):
	if name == "WorkerManager":
		from app.workers.manager import WorkerManager

		return WorkerManager
	if name == "run_worker":
		from app.workers.worker import run_worker

		return run_worker
	raise AttributeError(name)
