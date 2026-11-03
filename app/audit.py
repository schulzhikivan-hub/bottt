"""Administrator identity and the action journal (ТЗ § 10, § 16.3)."""
from sqlalchemy.orm import Session

from app.models import AuditLog
from app.payments import now


def log_action(db: Session, actor: str, action: str, object_type: str, object_id: str,
               details: str = "") -> None:
    db.add(AuditLog(actor=actor, action=action, object_type=object_type,
                    object_id=str(object_id), details=details[:2000], created_at=now()))
