"""Authentication and authorization tests without a live PostgreSQL server."""

from __future__ import annotations

import unittest
import uuid
from dataclasses import replace
from unittest.mock import patch

from app import create_app
from app.auth.jwt import issue_access_token
from app.models import User, UserRole


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

    def test_me_requires_authentication(self) -> None:
        response = self.client.get("/api/v1/auth/me")
        self.assertEqual(401, response.status_code)

    def test_me_does_not_return_hash(self) -> None:
        with patch("app.auth.decorators.authenticate_request", return_value=(self.user, None)):
            response = self.client.get("/api/v1/auth/me")
        self.assertEqual(200, response.status_code)
        self.assertNotIn("password_hash", response.get_json()["data"])

    def test_expired_token_is_rejected(self) -> None:
        with patch("app.auth.jwt.authenticate_request", return_value=(None, "token_expired")):
            response = self.client.get("/api/v1/auth/me")
        self.assertEqual(401, response.status_code)


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

    def test_viewer_cannot_create_user(self) -> None:
        viewer = User(id=uuid.uuid4(), username="viewer", email="viewer@example.com", password_hash="hash", role=UserRole.VIEWER, is_active=True)
        with patch("app.auth.decorators.authenticate_request", return_value=(viewer, None)):
            response = self.client.post("/api/v1/users", json={})
        self.assertEqual(403, response.status_code)
