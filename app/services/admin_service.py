"""Transactional operational controls backed by PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database.session import session_scope
from app.models import AuditEntityType, AuditEventType, SystemSetting
from app.services.audit_service import record_current
from app.sockets import publish_event

QUEUE_SETTING = "queue_control"


class AdminControlError(RuntimeError):
    pass


class AdminControlDatabaseError(AdminControlError):
    pass


def set_queue_paused(*, paused: bool) -> dict[str, Any]:
    try:
        with session_scope() as session:
            setting = session.scalar(select(SystemSetting).where(SystemSetting.key == QUEUE_SETTING).with_for_update())
            current = bool(setting and setting.value.get("paused", False))
            if current == paused:
                return {"paused": current, "result": "already_paused" if paused else "already_running"}
            if setting is None:
                setting = SystemSetting(key=QUEUE_SETTING, value={})
                session.add(setting)
            setting.value = {"paused": paused, "paused_at": datetime.now(timezone.utc).isoformat() if paused else None}
            setting.updated_by = str(_actor_id()) if _actor_id() else None
            record_current(session, event_type=AuditEventType.QUEUE_PAUSED if paused else AuditEventType.QUEUE_RESUMED, entity_type=AuditEntityType.SYSTEM, details={"action": "pause" if paused else "resume"})
            session.commit()
            result = {"paused": paused, "result": "paused" if paused else "resumed"}
    except SQLAlchemyError as exc:
        raise AdminControlDatabaseError from exc
    publish_event("queue_paused" if paused else "queue_resumed", result)
    return result


def queue_status() -> dict[str, Any]:
    try:
        with session_scope() as session:
            setting = session.get(SystemSetting, QUEUE_SETTING)
            value = setting.value if setting else {}
            return {"paused": bool(value.get("paused", False)), "paused_at": value.get("paused_at"), "paused_by": setting.updated_by if setting else None}
    except SQLAlchemyError as exc:
        raise AdminControlDatabaseError from exc


def queue_is_paused(session) -> bool:
    setting = session.scalar(select(SystemSetting).where(SystemSetting.key == QUEUE_SETTING).with_for_update())
    return bool(setting and setting.value.get("paused", False))


def _actor_id() -> uuid.UUID | None:
    try:
        from flask import g, has_request_context
        return getattr(g.current_user, "id", None) if has_request_context() else None
    except RuntimeError:
        return None