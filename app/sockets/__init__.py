"""Socket.IO extension and event publication infrastructure."""

import logging

import jwt
from flask import current_app, session
from flask_socketio import SocketIO

from app.database.session import session_scope
from app.models import User

socketio = SocketIO(async_mode="threading")
logger = logging.getLogger("taskforge.sockets")


@socketio.on("connect")
def authenticate_socket(auth):
    """Require a valid user token for externally initiated Socket.IO sessions."""
    token = auth.get("token") if isinstance(auth, dict) else None
    if not token:
        return False
    try:
        settings = current_app.config["TASKFORGE_SETTINGS"]
        claims = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
        user_id = claims.get("sub")
        with session_scope() as database_session:
            user = database_session.get(User, user_id)
            if user is None or not user.is_active:
                return False
            session["user_id"] = str(user.id)
            session["role"] = user.role.value
            return True
    except (jwt.InvalidTokenError, TypeError, ValueError):
        return False


def init_socketio(app):
    """Initialize the shared Socket.IO extension with a Flask app."""
    socketio.init_app(app)
    return socketio


def publish_event(event: str, payload: dict) -> None:
    """Publish a lifecycle event without turning notifications into state failures."""
    try:
        socketio.emit(event, payload)
    except Exception:
        logger.exception("Socket.IO event publication failed: %s", event)


def broadcast_server_shutdown() -> None:
    """Broadcast server shutdown notification to all connected clients."""
    try:
        socketio.emit("server:shutdown", {"message": "Server is shutting down."}, broadcast=True, skip_sid=None)
        logger.info("Server shutdown notification broadcast to clients")
    except Exception:
        logger.exception("Failed to broadcast server shutdown event")


__all__ = ["init_socketio", "publish_event", "broadcast_server_shutdown", "socketio"]
