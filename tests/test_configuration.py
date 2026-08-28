"""Configuration validation and isolation tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config.settings import ConfigurationError, Settings


BASE_ENV = {
    "DATABASE_URL": "postgresql+psycopg://user:pass@host/db",
    "JWT_SECRET_KEY": "a-development-secret-that-is-long-enough",
}


class ConfigurationTests(unittest.TestCase):
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

    def test_wildcard_credentialed_cors_is_rejected(self) -> None:
        with patch.dict(os.environ, {**BASE_ENV, "CORS_ORIGINS": "*", "CORS_SUPPORTS_CREDENTIALS": "true"}, clear=False):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment()

    def test_environment_name_is_allowlisted(self) -> None:
        with patch.dict(os.environ, {**BASE_ENV, "APP_ENV": "staging"}, clear=False):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment()
