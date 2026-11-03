"""Blocking people: presets, a cascade the moderator can choose, and an appeal from the blocked person.

Presets (ready terms): spam · fraud · abuse · rules. A permanent block is possible only for administrators; support and
moderators give a block for a time (1/3/7/30 days or their own number up to 365).
The three clocks do not fight each other: the block ends by its date; an appeal the person wrote stays open while the block
lasts; when the date comes and the appeal is still open the block either waits for the answer (Rules → «Блокировки») or ends
and the appeal is closed with the note «блок истёк»."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import admin_ops as ops
from app.admin_auth import SUPPORT, staff
from app.audit import log_action
from app.auth import current_user_id
from app.database import get_db
from app.models import Admin, Ticket, TicketMessage, User
from app.payments import aware, now
from app import guard

router = APIRouter()
PRESETS = {
    "spam": {"title": "Спам", "days": 3, "reason": "Спам и навязчивая реклама", "cascade": {"listings": True, "resume": True, "companies": False, "refund": True}},
    "fraud": {"title": "Мошенничество", "days": None, "reason": "Мошенничество", "cascade": {"listings": True, "resume": True, "companies": True, "refund": True}},
    "abuse": {"title": "Оскорбления", "days": 7, "reason": "Оскорбления и грубость", "cascade": {"listings": False, "resume": False, "companies": False, "refund": False}},
    "rules": {"title": "Нарушение правил", "days": 3, "reason": "Нарушение правил площадки", "cascade": {"listings": True, "resume": True, "companies": False, "refund": True}},
}
CASCADE_TEXT = {"listings": "Скрыть его личные объявления", "resume": "Скрыть резюме", "companies": "Приостановить его компании",
                "refund": "Вернуть на баланс неиспользованное платное время"}


@router.get("/api/admin/block-presets")
def presets(actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    boss = db.get(Admin, actor)
    return {"can_permanent": boss is not None and boss.role in {"admin", "super_admin"}, "cascade_text": CASCADE_TEXT,
            "presets": [{"key": k, **{x: v[x] for x in ("title", "days", "reason", "cascade")}} for k, v in PRESETS.items()]}


class BlockIn(BaseModel):
    preset: str = Field(default="", max_length=12)
    days: int | None = Field(default=None, ge=1, le=365)
    permanent: bool = False
    reason: str = Field(default="", max_length=300)
    cascade: dict[str, bool] = {}


@router.post("/api/admin/users/{tg_id}/block-preset")
def block_with_terms(tg_id: str, payload: BlockIn, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    preset = PRESETS.get(payload.preset)
    if payload.preset and preset is None:
        raise HTTPException(400, "Неизвестный пресет")
    boss = db.get(Admin, actor)
    permanent = payload.permanent or (preset is not None and preset["days"] is None and payload.days is None)
    if permanent and (boss is None or boss.role not in {"admin", "super_admin"}):
        raise HTTPException(403, "Бессрочно блокировать могут только администраторы: выберите срок")
    days = None if permanent else (payload.days or (preset["days"] if preset else None))
    if days is None and not permanent:
        raise HTTPException(400, "Укажите срок блокировки или отметьте «бессрочно»")
    reason = payload.reason.strip() or (preset["reason"] if preset else "")
    cascade = {**(preset["cascade"] if preset else {}), **{k: v for k, v in payload.cascade.items() if k in CASCADE_TEXT}}
    try:
        user = ops.block_user(db, actor, tg_id, reason, cascade)
    except ops.OpError as exc:
        raise HTTPException(exc.status, exc.message)
    user.blocked_until = None if permanent else now() + dt.timedelta(days=days)
    log_action(db, actor, "user.block_terms", "user", tg_id, f"{payload.preset or 'своё'}: {'бессрочно' if permanent else str(days) + ' дн.'}")
    db.commit()
    return {"tg_id": tg_id, "blocked": True, "until": user.blocked_until.isoformat() if user.blocked_until else None}


# ---------- the blocked person ----------
@router.get("/api/me/block-status")
def block_status(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None or not u.blocked:
        return {"blocked": False}
    appeal = db.scalar(select(Ticket).where(Ticket.user_tg_id == user_id, Ticket.kind == "appeal", Ticket.listing_id.is_(None), Ticket.status == "open"))
    return {"blocked": True, "reason": u.block_reason, "until": u.blocked_until.isoformat() if u.blocked_until else None, "appeal_open": appeal is not None}


class AppealIn(BaseModel):
    text: str = Field(min_length=10, max_length=1500)


@router.post("/api/me/block-appeal", status_code=201)
def block_appeal(payload: AppealIn, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """A blocked person can not use the app, but can contest the block: one open appeal at a time."""
    u = db.get(User, user_id)
    if u is None or not u.blocked:
        raise HTTPException(409, "Ваш аккаунт не заблокирован")
    guard.rate_limit(user_id, "block_appeal", 3, 86400)
    if db.scalar(select(Ticket.id).where(Ticket.user_tg_id == user_id, Ticket.kind == "appeal", Ticket.listing_id.is_(None), Ticket.status == "open")):
        raise HTTPException(409, "Обращение уже отправлено и рассматривается")
    t = Ticket(user_tg_id=user_id, kind="appeal", listing_id=None)
    db.add(t)
    db.flush()
    db.add(TicketMessage(ticket_id=t.id, staff=False, author=user_id, text=f"Оспаривание блокировки. {payload.text.strip()}"))
    db.commit()
    from app import adminplus
    adminplus.notify_staff(db, "Оспаривание блокировки ждёт ответа (админка → Поддержка).", "appeal", ("admin", "super_admin", "moderator"))
    return {"id": t.id}
