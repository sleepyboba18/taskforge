"""Configuration validation and isolation tests."""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from app import create_app
from app.config.settings import ConfigurationError, Settings


BASE_ENV = {
    "DATABASE_URL": "postgresql+psycopg://user:pass@host/db",
    "JWT_SECRET_KEY": "a-development-secret-that-is-long-enough",
}


class ConfigurationTests(unittest.TestCase):
    def test_application_passes_pool_settings_to_database_engine(self) -> None:
        settings = replace(
            Settings.from_environment(),
            db_pool_size=4,
            db_max_overflow=2,
            db_pool_timeout=17,
            db_pool_recycle=900,
        )

        with patch("app.initialize_database") as initialize_database:
            create_app(settings)

        initialize_database.assert_called_once_with(
            settings.database_url,
            pool_size=4,
            max_overflow=2,
            pool_timeout=17,
            pool_recycle=900,
        )

    def test_invalid_database_scheme_is_rejected(self) -> None:
        with patch.dict(os.environ, {**BASE_ENV, "DATABASE_URL": "sqlite:///taskforge.db"}, clear=False):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment()

    def test_port_upper_bound_is_rejected(self) -> None:
        with patch.dict(os.environ, {**BASE_ENV, "PORT": "65536"}, clear=False):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment()

    def test_production_security_defaults_are_rejected(self) -> None:
        with patch.dict(os.environ, {**BASE_ENV, "APP_ENV": "production", "SECRET_KEY": "change-me", "DEBUG": "true", "CORS_ORIGINS": "*"}, clear=False):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment()

    def test_production_rejects_weak_jwt_secret(self) -> None:
        with patch.dict(
            os.environ,
            {
                **BASE_ENV,
                "APP_ENV": "production",
                "SECRET_KEY": "s" * 40,
                "JWT_SECRET_KEY": "short",
                "DEBUG": "false",
                "CORS_ORIGINS": "https://taskforge.example",
            },
            clear=False,
        ):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment()

    def test_wildcard_credentialed_cors_is_rejected(self) -> None:
        with patch.dict(os.environ, {**BASE_ENV, "CORS_ORIGINS": "*", "CORS_SUPPORTS_CREDENTIALS": "true"}, clear=False):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment()

    def test_environment_name_is_allowlisted(self) -> None:
        with patch.dict(os.environ, {**BASE_ENV, "APP_ENV": "staging"}, clear=False):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment()
