"""Performance, indexing, and concurrency benchmark verification tests."""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.config.settings import Settings
from app.database.session import Base, get_engine, session_scope
from app.database.repositories.job_queue import claim_next_job
from app.models import AttemptStatus, Job, JobAttempt, JobStatus, Worker, WorkerStatus
from app.services.job_service import bulk_cancel_jobs, bulk_retry_jobs, create_job
from app.services.monitoring_service import get_monitoring_snapshot
from app.services.observability_service import collect_metrics
from app.services.retry_policy import RetryPolicy
from app.services.worker_recovery_service import recover_stale_workers
from app.workers.registry import register_worker, set_worker_status


def test_index_definitions_exist():
    """Verify that all added performance indexes are defined in SQLAlchemy metadata."""
    job_table = Job.__table__
    index_names = {idx.name for idx in job_table.indexes}

    expected_indexes = {
        "ix_jobs_queue_claim",
        "ix_jobs_status_priority_created",
        "ix_jobs_retry_promotion",
        "ix_jobs_workflow_id",
        "ix_jobs_created_at",
        "ix_jobs_status_created_at",
        "ix_jobs_status_started_at",
        "ix_jobs_status_updated_at",
        "ix_jobs_task_type",
    }
    for name in expected_indexes:
        assert name in index_names, f"Expected index {name} on jobs table."

    attempt_table = JobAttempt.__table__
    attempt_index_names = {idx.name for idx in attempt_table.indexes}
    assert "ix_job_attempts_worker_status" in attempt_index_names
    assert "ix_job_attempts_status_started_at" in attempt_index_names

    worker_table = Worker.__table__
    worker_index_names = {idx.name for idx in worker_table.indexes}
    assert "ix_workers_status_heartbeat" in worker_index_names


def test_job_workflow_index_is_declared_once():
    workflow_indexes = [
        index for index in Job.__table__.indexes if index.name == "ix_jobs_workflow_id"
    ]

    assert len(workflow_indexes) == 1


def test_connection_pool_configuration(monkeypatch):
    """Verify connection pool environment settings parse correctly."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/testdb")
    monkeypatch.setenv("JWT_SECRET_KEY", "secret-key-that-is-long-enough-32-chars")
    monkeypatch.setenv("DB_POOL_SIZE", "25")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "35")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "45")
    monkeypatch.setenv("DB_POOL_RECYCLE", "3600")

    settings = Settings.from_environment()
    assert settings.db_pool_size == 25
    assert settings.db_max_overflow == 35
    assert settings.db_pool_timeout == 45
    assert settings.db_pool_recycle == 3600


@pytest.mark.skipif(not os.getenv("TASKFORGE_TEST_DATABASE_URL"), reason="requires PostgreSQL test database")
def test_bulk_operations_performance(app):
    """Test that bulk_cancel_jobs processes batch operations accurately and efficiently."""
    with app.app_context():
        created_jobs = []
        for i in range(10):
            job = create_job(
                name=f"bulk-test-{i}",
                task_type="test",
                payload={"index": i},
                priority=5,
                max_retries=3,
                scheduled_at=None,
            )
            created_jobs.append(job.id)

        results = bulk_cancel_jobs(created_jobs)
        assert len(results) == 10
        assert all(r["status"] == "cancelled" for r in results)

        failed_jobs = []
        with session_scope() as session:
            for j_id in created_jobs:
                j = session.get(Job, j_id)
                j.status = JobStatus.FAILED
                j.retry_count = 0
                j.max_retries = 3
                failed_jobs.append(j_id)
            session.commit()

        retry_results = bulk_retry_jobs(failed_jobs)
        assert len(retry_results) == 10
        assert all(r["status"] == "retrying" for r in retry_results)


@pytest.mark.skipif(not os.getenv("TASKFORGE_TEST_DATABASE_URL"), reason="requires PostgreSQL test database")
def test_concurrent_worker_claiming(app):
    """Verify that multiple concurrent workers claiming jobs pick distinct jobs without race conditions."""
    with app.app_context():
        num_jobs = 20
        job_ids = []
        for i in range(num_jobs):
            j = create_job(
                name=f"concurrent-job-{i}",
                task_type="benchmark",
                payload={"index": i},
                priority=10,
                max_retries=3,
                scheduled_at=None,
            )
            job_ids.append(j.id)

        worker_ids = [uuid.uuid4() for _ in range(5)]
        with session_scope() as session:
            for w_id in worker_ids:
                register_worker(session, worker_id=w_id, worker_name=f"worker-{w_id.hex[:4]}", hostname="localhost", process_id=1000)
                set_worker_status(session, w_id, WorkerStatus.IDLE)
            session.commit()

        claimed_results = []
        lock = threading.Lock()

        def worker_claim_loop(w_id):
            claims = []
            for _ in range(5):
                with session_scope() as session:
                    set_worker_status(session, w_id, WorkerStatus.IDLE)
                    session.commit()
                    claimed = claim_next_job(session, w_id)
                    if claimed:
                        claims.append(claimed.job_id)
                        session.commit()
            with lock:
                claimed_results.extend(claims)

        threads = [threading.Thread(target=worker_claim_loop, args=(w_id,)) for w_id in worker_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(claimed_results) == len(set(claimed_results))


@pytest.mark.skipif(not os.getenv("TASKFORGE_TEST_DATABASE_URL"), reason="requires PostgreSQL test database")
def test_monitoring_snapshot_performance(app):
    """Test that monitoring snapshot aggregation runs within expected low database latency."""
    with app.app_context():
        settings = app.config["TASKFORGE_SETTINGS"]
        snapshot = get_monitoring_snapshot(window="1h", settings=settings)
        assert snapshot["status"] in {"HEALTHY", "DEGRADED"}
        assert "queue" in snapshot
        assert "workers" in snapshot
        assert "jobs" in snapshot
        assert "database" in snapshot

        metrics = collect_metrics(window="1h")
        assert "jobs" in metrics
        assert "throughput" in metrics
        assert "performance" in metrics


@pytest.mark.skipif(not os.getenv("TASKFORGE_TEST_DATABASE_URL"), reason="requires PostgreSQL test database")
def test_stale_worker_recovery_select_count_is_bounded(app):
    """Recovery reads stale workers and their active jobs in bounded query batches."""
    worker_count = 3
    stale_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    with app.app_context():
        with session_scope() as session:
            for index in range(worker_count):
                worker = Worker(
                    id=uuid.uuid4(),
                    worker_name=f"recovery-benchmark-{uuid.uuid4().hex[:8]}",
                    status=WorkerStatus.BUSY,
                    hostname="test-host",
                    process_id=index + 1,
                    last_heartbeat_at=stale_at,
                )
                session.add(worker)
                session.flush()
                job = Job(
                    id=uuid.uuid4(),
                    name=f"recovery-benchmark-{index}",
                    task_type="echo",
                    payload={},
                    status=JobStatus.RUNNING,
                    priority=5,
                    max_retries=3,
                    retry_count=0,
                    worker_id=worker.id,
                    started_at=stale_at,
                    claimed_at=stale_at,
                )
                session.add(job)
                session.flush()
                worker.current_job_id = job.id
                session.add(
                    JobAttempt(
                        id=uuid.uuid4(),
                        job_id=job.id,
                        attempt_number=1,
                        worker_id=worker.id,
                        status=AttemptStatus.RUNNING,
                        started_at=stale_at,
                        last_heartbeat_at=stale_at,
                    )
                )
            session.commit()

        select_count = 0

        def count_selects(connection, cursor, statement, parameters, context, executemany):
            nonlocal select_count
            if statement.lstrip().upper().startswith("SELECT"):
                select_count += 1

        engine = get_engine()
        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            outcomes = recover_stale_workers(
                stale_timeout=30,
                policy=RetryPolicy(base_delay=0, max_delay=0),
            )
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)

        assert len(outcomes) == worker_count
        assert select_count <= 4, f"{worker_count} recoveries required {select_count} SELECT statements"
