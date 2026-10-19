"""SQLAlchemy session lifecycle behavior tests."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.database.session import session_scope


class SessionScopeTests(unittest.TestCase):
    @patch("app.database.session.get_session_factory")
    def test_exception_rolls_back_and_closes_session(self, get_factory) -> None:
        session = MagicMock()
        get_factory.return_value.return_value = session

        with self.assertRaisesRegex(RuntimeError, "controlled failure"):
            with session_scope():
                raise RuntimeError("controlled failure")

        session.rollback.assert_called_once_with()
        session.close.assert_called_once_with()

    @patch("app.database.session.get_session_factory")
    def test_success_closes_session_without_implicit_commit(self, get_factory) -> None:
        session = MagicMock()
        get_factory.return_value.return_value = session

        with session_scope() as yielded:
            self.assertIs(session, yielded)

        session.commit.assert_not_called()
        session.rollback.assert_not_called()
        session.close.assert_called_once_with()