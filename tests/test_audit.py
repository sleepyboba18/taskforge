"""Focused audit model and API contract tests."""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from app.api.audit import _parse_filters
from app.models import AuditActorType, AuditEntityType, AuditEvent, AuditEventType


class AuditModelTests(unittest.TestCase):
    def test_event_uses_controlled_types_and_safe_details(self) -> None:
        event = AuditEvent(
            event_type=AuditEventType.JOB_CREATED,
            entity_type=AuditEntityType.JOB,
            actor_type=AuditActorType.USER,
            details={"workflow_id": str(uuid.uuid4())},
        )
        self.assertEqual("JOB_CREATED", event.event_type.value)
        self.assertNotIn("password", event.details)


class AuditFilterTests(unittest.TestCase):
    def test_utc_filter_is_parsed(self) -> None:
        from flask import Flask

        app = Flask(__name__)
        with app.test_request_context("/?created_after=2026-08-28T10:00:00Z"):
            filters = _parse_filters()
        self.assertEqual(datetime(2026, 8, 28, 10, tzinfo=timezone.utc), filters["created_after"])

    def test_invalid_filter_is_rejected(self) -> None:
        from flask import Flask

        app = Flask(__name__)
        with app.test_request_context("/?event_type=NOT_REAL"):
            self.assertIsNone(_parse_filters())
