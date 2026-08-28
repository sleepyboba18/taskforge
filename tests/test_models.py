"""Tests for the PostgreSQL-first ORM foundation."""

from __future__ import annotations

import os
import unittest
import uuid

from sqlalchemy import inspect

from app.database.session import Base
from app.models import AttemptStatus, Job, JobAttempt, JobStatus, Worker, WorkerStatus


class ModelMetadataTests(unittest.TestCase):
    def test_all_models_are_registered(self) -> None:
        self.assertEqual(
            {"jobs", "job_attempts", "workers", "recurring_jobs", "dead_letter_jobs", "users", "rate_limit_records"},
            set(Base.metadata.tables),
        )

    def test_uuid_defaults_are_uuid_factories(self) -> None:
        self.assertIsInstance(Job.id.default.arg(None), uuid.UUID)
        self.assertIsInstance(JobAttempt.id.default.arg(None), uuid.UUID)
        self.assertIsInstance(Worker.id.default.arg(None), uuid.UUID)

    def test_job_columns_and_defaults(self) -> None:
        self.assertEqual(JobStatus.PENDING.value, "PENDING")
        self.assertEqual(Job.priority.default.arg, 5)
        self.assertEqual(Job.max_retries.default.arg, 3)
        self.assertEqual(Job.retry_count.default.arg, 0)
        self.assertIsInstance(Job.__table__.c.payload.type, object)
        self.assertTrue(Job.__table__.c.created_at.type.timezone)
        self.assertTrue(Job.__table__.c.updated_at.type.timezone)

    def test_relationships_are_bidirectional(self) -> None:
        self.assertEqual(
            {"worker", "attempts", "recurring_job", "dead_letter"},
            set(Job.__mapper__.relationships.keys()),
        )
        self.assertEqual({"job", "worker"}, set(JobAttempt.__mapper__.relationships.keys()))
        self.assertEqual({"jobs", "attempts"}, set(Worker.__mapper__.relationships.keys()))

    def test_controlled_status_values(self) -> None:
        self.assertEqual(7, len(JobStatus))
        self.assertEqual(3, len(AttemptStatus))
        self.assertEqual(7, len(WorkerStatus))

    def test_constraints_and_indexes_exist(self) -> None:
        constraint_names = {constraint.name for constraint in Job.__table__.constraints}
        self.assertIn("ck_jobs_priority_non_negative", constraint_names)
        self.assertIn("ck_jobs_max_retries_non_negative", constraint_names)
        self.assertIn("ck_jobs_retry_count_non_negative", constraint_names)
        self.assertIn("uq_job_attempts_job_attempt_number", {constraint.name for constraint in JobAttempt.__table__.constraints})
        index_names = {index.name for index in Job.__table__.indexes}
        self.assertIn("ix_jobs_queue_claim", index_names)


@unittest.skipUnless(os.getenv("TASKFORGE_TEST_DATABASE_URL"), "requires PostgreSQL test database")
class PostgreSQLConstraintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from sqlalchemy import create_engine

        cls.engine = create_engine(os.environ["TASKFORGE_TEST_DATABASE_URL"])
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        Base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def test_database_inspector_sees_expected_tables(self) -> None:
        self.assertEqual(
            {"jobs", "job_attempts", "workers"},
            set(inspect(self.engine).get_table_names()) & {"jobs", "job_attempts", "workers"},
        )
