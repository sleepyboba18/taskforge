"""Transactional user and authentication operations."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth.password import hash_password, verify_password
from app.database.session import session_scope
from app.models import User, UserRole

logger = logging.getLogger("taskforge.users")


class UserServiceError(RuntimeError):
    """Base class for expected user-service errors."""


class UserAlreadyExistsError(UserServiceError):
    """Raised when username or email is already used."""


class UserNotFoundError(UserServiceError):
    """Raised when a user does not exist."""


class InvalidCredentialsError(UserServiceError):
    """Raised without revealing whether username or password was wrong."""


class UserDatabaseError(UserServiceError):
    """Raised when user persistence fails."""


_DUMMY_PASSWORD_HASH = hash_password("taskforge-invalid-login-password")


def authenticate_user(username: str, password: str) -> User:
    """Authenticate without revealing user existence to the caller."""
    try:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.username == username))
            password_hash = user.password_hash if user else _DUMMY_PASSWORD_HASH
            valid = verify_password(password_hash, password)
            if user is None or not valid or not user.is_active:
                logger.info("Failed login attempt for username: %s", username)
                raise InvalidCredentialsError
            user.last_login_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(user)
            session.expunge(user)
            logger.info("Successful login: user_id=%s", user.id)
            return user
    except InvalidCredentialsError:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Database error during authentication")
        raise UserDatabaseError from exc


def create_user(*, username: str, email: str, password: str, role: UserRole) -> User:
    user = User(username=username, email=email, password_hash=hash_password(password), role=role, is_active=True)
    try:
        with session_scope() as session:
            session.add(user)
            session.commit()
            session.refresh(user)
            session.expunge(user)
            return user
    except IntegrityError as exc:
        logger.info("Duplicate user creation rejected")
        raise UserAlreadyExistsError from exc
    except SQLAlchemyError as exc:
        logger.exception("Database error creating user")
        raise UserDatabaseError from exc


def list_users() -> list[User]:
    try:
        with session_scope() as session:
            users = list(session.scalars(select(User).order_by(User.created_at.desc())))
            for user in users:
                session.expunge(user)
            return users
    except SQLAlchemyError as exc:
        raise UserDatabaseError from exc


def get_user(user_id: uuid.UUID) -> User:
    try:
        with session_scope() as session:
            user = session.get(User, user_id)
            if user is None:
                raise UserNotFoundError
            session.expunge(user)
            return user
    except UserNotFoundError:
        raise
    except SQLAlchemyError as exc:
        raise UserDatabaseError from exc


def update_user(user_id: uuid.UUID, *, email: str | None = None, role: UserRole | None = None, is_active: bool | None = None) -> User:
    try:
        with session_scope() as session:
            user = session.get(User, user_id)
            if user is None:
                raise UserNotFoundError
            if email is not None:
                user.email = email
            if role is not None:
                user.role = role
            if is_active is not None:
                user.is_active = is_active
            user.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(user)
            session.expunge(user)
            return user
    except UserNotFoundError:
        raise
    except IntegrityError as exc:
        raise UserAlreadyExistsError from exc
    except SQLAlchemyError as exc:
        raise UserDatabaseError from exc


def change_password(user_id: uuid.UUID, current_password: str, new_password: str) -> None:
    try:
        with session_scope() as session:
            user = session.get(User, user_id)
            if user is None or not verify_password(user.password_hash, current_password):
                raise InvalidCredentialsError
            user.password_hash = hash_password(new_password)
            user.updated_at = datetime.now(timezone.utc)
            session.commit()
            logger.info("Password changed: user_id=%s", user_id)
    except InvalidCredentialsError:
        raise
    except SQLAlchemyError as exc:
        raise UserDatabaseError from exc


def bootstrap_admin(*, username: str, email: str, password: str) -> User | None:
    """Create the configured first admin only when no matching user exists."""
    try:
        with session_scope() as session:
            existing = session.scalar(select(User).where((User.username == username) | (User.email == email)))
            if existing is not None:
                return None
            user = User(
                username=username, email=email, password_hash=hash_password(password),
                role=UserRole.ADMIN, is_active=True,
            )
            session.add(user)
            session.commit()
            logger.info("Bootstrap administrator created: user_id=%s", user.id)
            return user
    except IntegrityError as exc:
        raise UserAlreadyExistsError from exc
    except SQLAlchemyError as exc:
        logger.exception("Database error bootstrapping administrator")
        raise UserDatabaseError from exc
