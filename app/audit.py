"""Administrator identity and the action journal (ТЗ § 10, § 16.3)."""
import re

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuditLog
from app.payments import now


def admin_actor(x_admin_key: str | None = Header(default=None),
                x_admin_name: str | None = Header(default=None)) -> str:
    """Check the admin key and return the actor's name for the journal.

    Temporary model: one shared key plus a name typed by the administrator.
    Per-person accounts with codes from the bot are a separate step (see the admin plan).
    """
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(401, "Нужен админ-ключ")
    name = (x_admin_name or "").strip()
    # HTTP headers carry Latin letters only: the name is typed in Latin (e.g. "Ivan")
    if not re.fullmatch(r"[A-Za-z0-9 ._-]{2,80}", name):
        raise HTTPException(400, "Укажите имя администратора латиницей в заголовке X-Admin-Name")
    return name


def log_action(db: Session, actor: str, action: str, object_type: str, object_id: str,
               details: str = "") -> None:
    db.add(AuditLog(actor=actor, action=action, object_type=object_type,
                    object_id=str(object_id), details=details[:2000], created_at=now()))
