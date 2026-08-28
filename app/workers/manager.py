"""Multiprocessing manager for TaskForge workers."""

from __future__ import annotations

import logging
import multiprocessing

from app.config.settings import Settings
from app.workers.worker import run_worker
from app.workers.retry_scheduler import run_retry_scheduler
from app.workers.scheduled_scheduler import run_scheduled_scheduler
from app.workers.recurring_scheduler import run_recurring_scheduler
from app.workers.recovery_scheduler import run_recovery_scheduler

logger = logging.getLogger("taskforge.worker_manager")


class WorkerManager:
    """Start, monitor, and gracefully stop independent worker processes."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._shutdown_event = multiprocessing.Event()
        self._processes: list[multiprocessing.Process] = []
        self._started = False

    @property
    def processes(self) -> tuple[multiprocessing.Process, ...]:
        return tuple(self._processes)

    def start(self) -> None:
        """Start the configured number of worker children once."""
        if self._started:
            return
        self._started = True
        for index in range(1, self.settings.worker_count + 1):
            process = multiprocessing.Process(
                target=run_worker,
                kwargs={
                    "database_url": self.settings.database_url,
                    "worker_name": f"taskforge-worker-{index}",
                    "poll_interval": self.settings.worker_poll_interval,
                    "retry_base_delay": self.settings.retry_base_delay,
                    "retry_max_delay": self.settings.retry_max_delay,
                    "heartbeat_interval": self.settings.worker_heartbeat_interval,
                    "max_dependency_propagation_depth": self.settings.max_dependency_propagation_depth,
                    "shutdown_event": self._shutdown_event,
                },
                name=f"taskforge-worker-{index}",
            )
            process.start()
            self._processes.append(process)
            logger.info("Started worker process %s", process.name)
        scheduler = multiprocessing.Process(
            target=run_retry_scheduler,
            kwargs={
                "database_url": self.settings.database_url,
                "poll_interval": self.settings.retry_poll_interval,
                "batch_size": self.settings.retry_batch_size,
                "shutdown_event": self._shutdown_event,
            },
            name="taskforge-retry-scheduler",
        )
        scheduler.start()
        self._processes.append(scheduler)
        logger.info("Started retry scheduler process")
        scheduled_scheduler = multiprocessing.Process(
            target=run_scheduled_scheduler,
            kwargs={
                "database_url": self.settings.database_url,
                "poll_interval": self.settings.scheduler_poll_interval,
                "batch_size": self.settings.scheduler_batch_size,
                "shutdown_event": self._shutdown_event,
            },
            name="taskforge-scheduled-scheduler",
        )
        scheduled_scheduler.start()
        self._processes.append(scheduled_scheduler)
        logger.info("Started scheduled-job scheduler process")
        recurring_scheduler = multiprocessing.Process(
            target=run_recurring_scheduler,
            kwargs={
                "database_url": self.settings.database_url,
                "poll_interval": self.settings.scheduler_poll_interval,
                "batch_size": self.settings.scheduler_batch_size,
                "shutdown_event": self._shutdown_event,
            },
            name="taskforge-recurring-scheduler",
        )
        recurring_scheduler.start()
        self._processes.append(recurring_scheduler)
        logger.info("Started recurring-job scheduler process")
        recovery_scheduler = multiprocessing.Process(
            target=run_recovery_scheduler,
            kwargs={
                "database_url": self.settings.database_url,
                "poll_interval": self.settings.recovery_poll_interval,
                "stale_timeout": self.settings.worker_stale_timeout,
                "retry_base_delay": self.settings.retry_base_delay,
                "retry_max_delay": self.settings.retry_max_delay,
                "rate_limit_retention_seconds": self.settings.rate_limit_retention_seconds,
                "shutdown_event": self._shutdown_event,
            },
            name="taskforge-recovery-scheduler",
        )
        recovery_scheduler.start()
        self._processes.append(recovery_scheduler)
        logger.info("Started recovery scheduler process")

    def stop(self) -> None:
        """Request graceful shutdown, then terminate only stragglers."""
        if not self._started:
            return
        self._shutdown_event.set()
        for process in self._processes:
            process.join(timeout=self.settings.worker_shutdown_timeout)
        for process in self._processes:
            if process.is_alive():
                logger.warning("Terminating unresponsive worker process %s", process.name)
                process.terminate()
                process.join()
        self._processes.clear()
        self._started = False
