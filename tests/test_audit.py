"""Focused audit model and API contract tests."""

from __future__ import annotations

import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from app import create_app
from app.api.audit import _parse_filters
from app.models import AuditActorType, AuditEntityType, AuditEvent, AuditEventType, User, UserRole


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


class AuditRouteValidationTests(unittest.TestCase):
    def test_unknown_and_repeated_filters_are_rejected(self) -> None:
        app = create_app()
        app.config["TASKFORGE_SETTINGS"] = replace(
            app.config["TASKFORGE_SETTINGS"], rate_limit_enabled=False
        )
        client = app.test_client()
        operator = User(
            id=uuid.uuid4(), username="operator", email="operator@example.com",
            password_hash="fake-test-hash", role=UserRole.OPERATOR, is_active=True,
        )
        with patch("app.auth.decorators.authenticate_request", return_value=(operator, None)), patch(
            "app.api.audit.list_events"
        ) as list_events:
            unknown = client.get("/api/v1/audit-events?sort=anything")
            repeated = client.get("/api/v1/audit-events?page=1&page=2")

        self.assertEqual(400, unknown.status_code)
        self.assertEqual(400, repeated.status_code)
        list_events.assert_not_called()
