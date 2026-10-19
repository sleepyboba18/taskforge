"""Authentication and authorization tests without a live PostgreSQL server."""

from __future__ import annotations

import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt

from app import create_app
from app.auth.jwt import ALGORITHM, issue_access_token
from app.models import User, UserRole
from app.sockets import broadcast_server_shutdown, socketio


class AuthRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app()
        cls.app.config["TASKFORGE_SETTINGS"] = replace(cls.app.config["TASKFORGE_SETTINGS"], rate_limit_enabled=False)
        cls.client = cls.app.test_client()
        cls.user = User(
            id=uuid.uuid4(), username="operator", email="operator@example.com",
            password_hash="scrypt:dummy", role=UserRole.OPERATOR, is_active=True,
        )

    def test_login_returns_bearer_token_without_password_data(self) -> None:
        with patch("app.api.auth.authenticate_user", return_value=self.user), patch(
            "app.api.auth.issue_access_token", return_value=("token", 3600)
        ):
            response = self.client.post("/api/v1/auth/login", json={"username": "operator", "password": "password"})
        self.assertEqual(200, response.status_code)
        data = response.get_json()["data"]
        self.assertEqual("Bearer", data["token_type"])
        self.assertNotIn("password", data)

    def test_invalid_credentials_are_generic(self) -> None:
        from app.services.user_service import InvalidCredentialsError

        with patch("app.api.auth.authenticate_user", side_effect=InvalidCredentialsError):
            response = self.client.post("/api/v1/auth/login", json={"username": "missing", "password": "password"})
        self.assertEqual(401, response.status_code)
        self.assertEqual("invalid_credentials", response.get_json()["error"]["code"])

    def test_failed_login_log_does_not_include_username(self) -> None:
        from app.services.user_service import InvalidCredentialsError, authenticate_user

        with patch("app.services.user_service.session_scope") as scope:
            scope.return_value.__enter__.return_value.scalar.return_value = None
            with self.assertLogs("taskforge.users", level="INFO") as logs:
                with self.assertRaises(InvalidCredentialsError):
                    authenticate_user("bad-user\nforged-line", "fake-password")

        self.assertNotIn("bad-user", "\n".join(logs.output))
        self.assertNotIn("forged-line", "\n".join(logs.output))

    def test_oversized_password_is_rejected_before_hash_verification(self) -> None:
        with patch("app.api.auth.authenticate_user") as authenticate:
            response = self.client.post(
                "/api/v1/auth/login",
                json={"username": "operator", "password": "x" * 1025},
            )

        self.assertEqual(400, response.status_code)
        authenticate.assert_not_called()

    def test_password_change_reports_oversized_current_password_field(self) -> None:
        with patch("app.auth.decorators.authenticate_request", return_value=(self.user, None)), patch(
            "app.api.auth.change_password"
        ) as change_password:
            response = self.client.post(
                "/api/v1/auth/change-password",
                json={"current_password": "x" * 1025, "new_password": "new-password-1"},
            )

        self.assertEqual(400, response.status_code)
        self.assertIn("current_password", response.get_json()["error"]["details"])
        change_password.assert_not_called()

    def test_me_requires_authentication(self) -> None:
        response = self.client.get("/api/v1/auth/me")
        self.assertEqual(401, response.status_code)
        body = response.get_json()
        self.assertEqual("authentication_required", body["error"]["code"])
        self.assertTrue(body["error"]["message"])
        self.assertEqual(response.headers["X-Request-ID"], body["request_id"])

    def test_me_does_not_return_hash(self) -> None:
        with patch("app.auth.decorators.authenticate_request", return_value=(self.user, None)):
            response = self.client.get("/api/v1/auth/me")
        self.assertEqual(200, response.status_code)
        self.assertNotIn("password_hash", response.get_json()["data"])

    def test_expired_token_is_rejected(self) -> None:
        with patch("app.auth.jwt.authenticate_request", return_value=(None, "token_expired")):
            response = self.client.get("/api/v1/auth/me")
        self.assertEqual(401, response.status_code)

    def test_security_headers_are_present(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(200, response.status_code)
        self.assertEqual("nosniff", response.headers.get("X-Content-Type-Options"))
        self.assertEqual("DENY", response.headers.get("X-Frame-Options"))
        self.assertEqual("no-referrer", response.headers.get("Referrer-Policy"))
        self.assertIn("default-src 'none'", response.headers.get("Content-Security-Policy", ""))

    def test_signed_token_with_invalid_claims_cannot_authenticate(self) -> None:
        now = datetime.now(timezone.utc)
        secret = self.app.config["TASKFORGE_SETTINGS"].jwt_secret_key
        valid_claims = {
            "sub": str(self.user.id),
            "user_id": str(self.user.id),
            "role": UserRole.OPERATOR.value,
            "token_type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=1),
        }

        for claims in (
            {**valid_claims, "user_id": str(uuid.uuid4())},
            {**valid_claims, "role": "ADMINISTRATOR"},
        ):
            with self.subTest(claims=claims):
                token = jwt.encode(claims, secret, algorithm=ALGORITHM)
                response = self.client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
                self.assertEqual(401, response.status_code)

    def test_socket_connection_rejects_missing_or_invalid_token(self) -> None:
        unauthenticated = socketio.test_client(self.app)
        self.assertFalse(unauthenticated.is_connected())

        invalid = socketio.test_client(self.app, auth={"token": "not-a-jwt"})
        self.assertFalse(invalid.is_connected())

    def test_socket_connection_accepts_valid_access_token(self) -> None:
        with self.app.app_context():
            token, _ = issue_access_token(self.user)

        with patch("app.sockets.session_scope") as session_scope:
            session_scope.return_value.__enter__.return_value.get.return_value = self.user
            connected = socketio.test_client(self.app, auth={"token": token})

        self.assertTrue(connected.is_connected())
        connected.disconnect()

    def test_shutdown_broadcast_reaches_connected_client(self) -> None:
        with self.app.app_context():
            token, _ = issue_access_token(self.user)

        with patch("app.sockets.session_scope") as session_scope:
            session_scope.return_value.__enter__.return_value.get.return_value = self.user
            connected = socketio.test_client(self.app, auth={"token": token})

        broadcast_server_shutdown()

        events = connected.get_received()
        self.assertTrue(
            any(event["name"] == "server:shutdown" for event in events),
            f"Expected server:shutdown for connected user {self.user.id}, received {events!r}",
        )
        connected.disconnect()


class AuthorizationRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app()
        cls.app.config["TASKFORGE_SETTINGS"] = replace(cls.app.config["TASKFORGE_SETTINGS"], rate_limit_enabled=False)
        cls.client = cls.app.test_client()

    def test_viewer_cannot_submit_job(self) -> None:
        viewer = User(id=uuid.uuid4(), username="viewer", email="viewer@example.com", password_hash="hash", role=UserRole.VIEWER, is_active=True)
        with patch("app.auth.decorators.authenticate_request", return_value=(viewer, None)):
            response = self.client.post("/api/v1/jobs", json={})
        self.assertEqual(403, response.status_code)
        self.assertEqual("forbidden", response.get_json()["error"]["code"])

    def test_admin_can_create_user(self) -> None:
        admin = User(id=uuid.uuid4(), username="admin", email="admin@example.com", password_hash="hash", role=UserRole.ADMIN, is_active=True)
        created = User(id=uuid.uuid4(), username="new", email="new@example.com", password_hash="hash", role=UserRole.OPERATOR, is_active=True)
        with patch("app.auth.decorators.authenticate_request", return_value=(admin, None)), patch(
            "app.api.users.create_user", return_value=created
        ):
            response = self.client.post("/api/v1/users", json={"username": "new", "email": "new@example.com", "password": "password1", "role": "OPERATOR"})
        self.assertEqual(201, response.status_code)
        self.assertNotIn("password_hash", response.get_json()["data"])

    def test_admin_user_update_rejects_invalid_email_and_empty_patch(self) -> None:
        admin = User(id=uuid.uuid4(), username="admin", email="admin@example.com", password_hash="hash", role=UserRole.ADMIN, is_active=True)
        user_id = uuid.uuid4()
        for body in ({}, {"email": ["not", "an", "email"]}, {"email": "invalid"}):
            with self.subTest(body=body), patch(
                "app.auth.decorators.authenticate_request", return_value=(admin, None)
            ), patch("app.api.users.update_user") as update_user:
                response = self.client.patch(f"/api/v1/users/{user_id}", json=body)
            self.assertEqual(400, response.status_code)
            update_user.assert_not_called()

    def test_admin_user_list_is_paginated(self) -> None:
        admin = User(id=uuid.uuid4(), username="admin", email="admin@example.com", password_hash="hash", role=UserRole.ADMIN, is_active=True)
        with patch("app.auth.decorators.authenticate_request", return_value=(admin, None)), patch(
            "app.api.users.list_users", return_value=([], 35)
        ) as list_users:
            response = self.client.get("/api/v1/users?page=2&per_page=10")

        self.assertEqual(200, response.status_code)
        self.assertEqual({"page": 2, "per_page": 10, "total": 35, "pages": 4}, response.get_json()["pagination"])
        list_users.assert_called_once_with(page=2, per_page=10)

    def test_admin_user_list_rejects_oversized_page(self) -> None:
        admin = User(id=uuid.uuid4(), username="admin", email="admin@example.com", password_hash="hash", role=UserRole.ADMIN, is_active=True)
        with patch("app.auth.decorators.authenticate_request", return_value=(admin, None)), patch(
            "app.api.users.list_users"
        ) as list_users:
            response = self.client.get("/api/v1/users?per_page=101")

        self.assertEqual(400, response.status_code)
        self.assertEqual("validation_error", response.get_json()["error"]["code"])
        list_users.assert_not_called()

    def test_viewer_cannot_create_user(self) -> None:
        viewer = User(id=uuid.uuid4(), username="viewer", email="viewer@example.com", password_hash="hash", role=UserRole.VIEWER, is_active=True)
        with patch("app.auth.decorators.authenticate_request", return_value=(viewer, None)):
            response = self.client.post("/api/v1/users", json={})
        self.assertEqual(403, response.status_code)
