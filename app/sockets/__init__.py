"""Socket.IO extension and event publication infrastructure."""

import logging

from flask_socketio import SocketIO

socketio = SocketIO(async_mode="threading")
logger = logging.getLogger("taskforge.sockets")


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


__all__ = ["init_socketio", "publish_event", "socketio"]
