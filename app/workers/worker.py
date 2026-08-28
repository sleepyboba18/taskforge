"""Independent PostgreSQL-backed worker process."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from app.database.repositories.job_queue import ClaimedJob, claim_next_job
from app.database.session import dispose_database, initialize_database, session_scope
from app.models import AttemptStatus, Job, JobAttempt, JobStatus, Worker, WorkerStatus
from app.services.task_executor import TaskRegistry
from app.services.retry_policy import RetryPolicy
from app.services.retry_service import RetryDatabaseError, handle_task_failure
from app.services.workflow_service import update_workflow_status
from app.sockets import publish_event
from app.workers.registry import heartbeat_worker, register_worker, set_worker_status

logger = logging.getLogger("taskforge.worker")


def run_worker(
    *,
    database_url: str,
    worker_name: str,
    poll_interval: float,
    retry_base_delay: float,
    retry_max_delay: float,
    heartbeat_interval: float,
    shutdown_event,
    max_dependency_propagation_depth: int = 50,
) -> None:
    """Run one worker process using resources initialized inside that process."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    dispose_database()
    initialize_database(database_url)
    worker_id = uuid.uuid4()
    instance_name = f"{worker_name}-{worker_id.hex[:8]}"
    registered = False
    try:
        _register_worker_until_success(
            worker_id, instance_name, shutdown_event
        )
        registered = True
        _set_status(worker_id, WorkerStatus.IDLE)
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            args=(worker_id, heartbeat_interval, heartbeat_stop),
            name=f"{worker_name}-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        publish_event(
            "worker:started",
            {"worker_id": str(worker_id), "worker_name": instance_name, "status": WorkerStatus.IDLE.value},
        )
        publish_event(
            "worker:registered",
            {"worker_id": str(worker_id), "worker_name": instance_name, "status": WorkerStatus.IDLE.value},
        )
        logger.info("Worker registered: %s (%s)", instance_name, worker_id)
        registry = TaskRegistry.builtins()
        retry_policy = RetryPolicy(retry_base_delay, retry_max_delay)
        while not shutdown_event.is_set():
            claimed = _claim(worker_id)
            if claimed is None:
                shutdown_event.wait(poll_interval)
                continue
            _set_status(worker_id, WorkerStatus.BUSY)
            publish_event("job:started", _job_event(claimed, JobStatus.RUNNING))
            logger.info("Job claimed: %s by %s", claimed.job_id, worker_id)
            try:
                registry.execute(claimed.task_type, claimed.payload)
            except Exception as exc:
                _fail_job(claimed, exc, retry_policy, max_dependency_propagation_depth)
            else:
                _complete_job(claimed)
    except Exception:
        logger.exception("Worker failed before clean shutdown: %s", worker_id)
        if registered:
            _set_status(worker_id, WorkerStatus.UNHEALTHY)
            publish_event("worker:unhealthy", {"worker_id": str(worker_id), "status": WorkerStatus.UNHEALTHY.value})
    finally:
        if registered:
            heartbeat_stop.set()
            if 'heartbeat_thread' in locals():
                heartbeat_thread.join(timeout=heartbeat_interval)
            _set_status(worker_id, WorkerStatus.STOPPING)
            _set_status(worker_id, WorkerStatus.STOPPED)
            publish_event("worker:stopped", {"worker_id": str(worker_id), "status": WorkerStatus.STOPPED.value})
        logger.info("Worker stopped: %s", worker_id)


def _register_worker_until_success(worker_id: uuid.UUID, worker_name: str, shutdown_event) -> None:
    while not shutdown_event.is_set():
        try:
            with session_scope() as session:
                register_worker(
                    session,
                    worker_id=worker_id,
                    worker_name=worker_name,
                    hostname=socket.gethostname(),
                    process_id=os.getpid(),
                )
                session.commit()
                return
        except SQLAlchemyError:
            logger.exception("Worker registration failed: %s", worker_id)
            shutdown_event.wait(2.0)
    raise RuntimeError("Worker shutdown requested before registration succeeded.")


def _heartbeat_loop(worker_id: uuid.UUID, interval: float, stop_event: threading.Event) -> None:
    while not stop_event.wait(interval):
        heartbeat_at = datetime.now(timezone.utc)
        try:
            with session_scope() as session:
                if not heartbeat_worker(session, worker_id, heartbeat_at):
                    logger.warning("Worker heartbeat registration lost: %s", worker_id)
                session.commit()
        except SQLAlchemyError:
            logger.exception("Worker heartbeat failed: %s", worker_id)


def _claim(worker_id: uuid.UUID) -> ClaimedJob | None:
    try:
        with session_scope() as session:
            claimed = claim_next_job(session, worker_id)
            session.commit()
            return claimed
    except SQLAlchemyError:
        logger.exception("Database polling error for worker %s", worker_id)
        time.sleep(1.0)
        return None


def _set_status(worker_id: uuid.UUID, status: WorkerStatus) -> None:
    try:
        with session_scope() as session:
            set_worker_status(session, worker_id, status)
            session.commit()
    except SQLAlchemyError:
        logger.exception("Unable to set worker %s status to %s", worker_id, status.value)


def _complete_job(claimed: ClaimedJob) -> None:
    now = datetime.now(timezone.utc)
    try:
        with session_scope() as session:
            job = session.get(Job, claimed.job_id)
            attempt = session.get(JobAttempt, claimed.attempt_id)
            if job is None or attempt is None:
                raise RuntimeError("Claimed job or attempt no longer exists.")
            job.status = JobStatus.COMPLETED
            job.completed_at = now
            job.updated_at = now
            attempt.status = AttemptStatus.COMPLETED
            attempt.finished_at = now
            attempt.last_heartbeat_at = now
            worker = session.get(Worker, claimed.worker_id)
            if worker is not None:
                worker.current_job_id = None
            set_worker_status(session, claimed.worker_id, WorkerStatus.IDLE)
            update_workflow_status(session, job.workflow_id)
            session.commit()
    except SQLAlchemyError:
        logger.exception("Database error completing job %s", claimed.job_id)
        return
    publish_event("job:completed", _job_event(claimed, JobStatus.COMPLETED))
    logger.info("Job execution completed: %s", claimed.job_id)


def _fail_job(claimed: ClaimedJob, error: Exception, policy: RetryPolicy, max_dependency_propagation_depth: int = 50) -> None:
    logger.exception("Job execution failed: %s", claimed.job_id)
    try:
        outcome = handle_task_failure(
            job_id=claimed.job_id,
            attempt_id=claimed.attempt_id,
            worker_id=claimed.worker_id,
            error=error,
            policy=policy,
            max_dependency_propagation_depth=max_dependency_propagation_depth,
        )
    except (RetryDatabaseError, RuntimeError):
        logger.exception("Database error failing job %s", claimed.job_id)
        return
    if outcome.status == JobStatus.RETRYING:
        publish_event(
            "job:retrying",
            {
                "id": str(outcome.job_id),
                "status": outcome.status.value,
                "retry_count": outcome.retry_count,
                "max_retries": outcome.max_retries,
                "next_retry_at": outcome.next_retry_at.isoformat() if outcome.next_retry_at else None,
            },
        )
        logger.info(
            "Retry scheduled: job=%s retry_count=%s next_retry_at=%s",
            outcome.job_id,
            outcome.retry_count,
            outcome.next_retry_at,
        )
    else:
        publish_event("job:failed", _job_event(claimed, JobStatus.FAILED))
        if outcome.dead_letter_id is not None:
            publish_event(
                "job:dead_lettered",
                {
                    "job_id": str(outcome.job_id),
                    "dead_letter_id": str(outcome.dead_letter_id),
                    "status": JobStatus.FAILED.value,
                    "attempt_count": outcome.attempt_number,
                },
            )
        logger.info("Retry exhausted or unavailable: %s", outcome.job_id)


def _job_event(claimed: ClaimedJob, status: JobStatus) -> dict[str, str]:
    return {"id": str(claimed.job_id), "status": status.value, "worker_id": str(claimed.worker_id)}
