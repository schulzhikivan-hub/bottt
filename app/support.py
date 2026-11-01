"""Support, appeals, FAQ, app texts and automatic moderation. Everything is edited in the admin panel.

Support works inside the app: a person writes a request, staff answers in the admin panel, the answer arrives in the
notifications and in the request itself. Nobody has to publish a personal Telegram nick."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import guard
from app.admin_auth import ADM, MOD, SUPPORT, staff
from app.audit import log_action
from app.database import get_db
from app.models import AuditLog, Faq, Listing, Setting, Ticket, TicketMessage
from app.payments import now
from app.users import writer

router = APIRouter()

DEFAULTS = {
    "welcome_title": "Добро пожаловать в KOREA HUB",
    "welcome_text": "Работа, жильё, барахолка и знакомства для русскоязычных в Южной Корее.\n"
                    "1. Смотрите объявления без регистрации.\n2. Чтобы написать автору или опубликовать своё, примите правила.\n"
                    "3. Все объявления проверяет модератор.",
    "support_note": "Мы отвечаем обычно в течение суток. Ответ придёт сюда и в уведомления.",
    "krw_rub": "", "krw_usd": "",
    "automod_enabled": "0", "automod_min_published": "3",
}
PUBLIC_KEYS = ("welcome_title", "welcome_text", "support_note")


def get_setting(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    return row.value if row is not None else DEFAULTS.get(key, "")


def all_settings(db: Session) -> dict:
    return {k: get_setting(db, k) for k in DEFAULTS}


def rates(db: Session) -> dict:
    """Won conversion set in the admin panel; .env values (older setups) are the fallback."""
    from app.config import settings
    out = {}
    for code, key, fallback in (("rub", "krw_rub", settings.krw_rub), ("usd", "krw_usd", settings.krw_usd)):
        raw = get_setting(db, key).replace(",", ".").strip()
        try:
            value = float(raw) if raw else float(fallback or 0)
        except ValueError:
            value = 0
        if value > 0:
            out[code] = value
    return out


# ---------- public ----------
@router.get("/api/app-texts")
def app_texts(db: Session = Depends(get_db)):
    return {k: get_setting(db, k) for k in PUBLIC_KEYS}


@router.get("/api/faq")
def faq(db: Session = Depends(get_db)):
    rows = db.scalars(select(Faq).where(Faq.active.is_(True)).order_by(Faq.position, Faq.id)).all()
    return [{"id": r.id, "question": r.question, "answer": r.answer} for r in rows]


# ---------- tickets (user side) ----------
class TicketIn(BaseModel):
    text: str = Field(min_length=5, max_length=1500)
    kind: str = "support"
    listing_id: int | None = None


class TicketReplyIn(BaseModel):
    text: str = Field(min_length=2, max_length=1500)


def _view(db: Session, t: Ticket, with_messages: bool = False) -> dict:
    out = {"id": t.id, "kind": t.kind, "listing_id": t.listing_id, "status": t.status,
           "created_at": t.created_at.isoformat(), "updated_at": t.updated_at.isoformat()}
    msgs = db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == t.id).order_by(TicketMessage.id)).all()
    out["preview"] = (msgs[0].text[:80] if msgs else "")
    if with_messages:
        out["messages"] = [{"staff": m.staff, "text": m.text, "at": m.created_at.isoformat()} for m in msgs]
    return out


@router.post("/api/tickets", status_code=201)
def create_ticket(payload: TicketIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    guard.rate_limit(user_id, "ticket", 5, 86400)
    if payload.kind not in {"support", "appeal"}:
        raise HTTPException(400, "Неизвестный вид обращения")
    if payload.kind == "appeal":
        item = db.get(Listing, payload.listing_id) if payload.listing_id else None
        if item is None or item.author_telegram_id != user_id or item.status not in {"rejected", "needs_fix", "hidden"}:
            raise HTTPException(400, "Оспорить можно своё отклонённое или скрытое объявление")
        if db.scalar(select(Ticket.id).where(Ticket.user_tg_id == user_id, Ticket.listing_id == item.id,
                                             Ticket.kind == "appeal", Ticket.status != "closed")):
            raise HTTPException(409, "Обращение по этому объявлению уже отправлено")
    t = Ticket(user_tg_id=user_id, kind=payload.kind, listing_id=payload.listing_id if payload.kind == "appeal" else None)
    db.add(t)
    db.flush()
    db.add(TicketMessage(ticket_id=t.id, staff=False, author=user_id, text=payload.text.strip()))
    db.commit()
    return {"id": t.id}


@router.get("/api/tickets")
def my_tickets(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    rows = db.scalars(select(Ticket).where(Ticket.user_tg_id == user_id).order_by(Ticket.updated_at.desc()).limit(50)).all()
    return [_view(db, t) for t in rows]


def _mine(db: Session, ticket_id: int, user_id: str) -> Ticket:
    t = db.get(Ticket, ticket_id)
    if t is None or t.user_tg_id != user_id:
        raise HTTPException(404, "Обращение не найдено")
    return t


@router.get("/api/tickets/{ticket_id}")
def my_ticket(ticket_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    return _view(db, _mine(db, ticket_id, user_id), True)


@router.post("/api/tickets/{ticket_id}/messages", status_code=201)
def ticket_write(ticket_id: int, payload: TicketReplyIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    guard.rate_limit(user_id, "ticket_msg", 20, 3600)
    t = _mine(db, ticket_id, user_id)
    db.add(TicketMessage(ticket_id=t.id, staff=False, author=user_id, text=payload.text.strip()))
    t.status, t.updated_at = "open", now()      # a new message reopens the request
    db.commit()
    return {"ok": True}


# ---------- tickets (staff side) ----------
@router.get("/api/admin/tickets")
def admin_tickets(status: str = Query(default="open"), actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    stmt = select(Ticket).order_by(Ticket.updated_at.desc()).limit(100)
    if status != "all":
        stmt = select(Ticket).where(Ticket.status == status).order_by(Ticket.updated_at.desc()).limit(100)
    return [{**_view(db, t), "user": t.user_tg_id} for t in db.scalars(stmt).all()]


@router.get("/api/admin/tickets/{ticket_id}")
def admin_ticket(ticket_id: int, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    t = db.get(Ticket, ticket_id)
    if t is None:
        raise HTTPException(404, "Обращение не найдено")
    out = {**_view(db, t, True), "user": t.user_tg_id}
    if t.listing_id:
        item = db.get(Listing, t.listing_id)
        if item is not None:
            out["listing"] = {"id": item.id, "title": item.title, "status": item.status, "note": item.note}
    return out


@router.post("/api/admin/tickets/{ticket_id}/reply")
def admin_reply(ticket_id: int, payload: TicketReplyIn, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    t = db.get(Ticket, ticket_id)
    if t is None:
        raise HTTPException(404, "Обращение не найдено")
    db.add(TicketMessage(ticket_id=t.id, staff=True, author=actor, text=payload.text.strip()))
    t.status, t.updated_at = "answered", now()
    log_action(db, actor, "ticket.reply", "ticket", ticket_id, "")
    db.commit()
    guard.notify_user(db, t.user_tg_id, "Поддержка ответила на ваше обращение. Откройте: Кабинет → Поддержка.", "support")
    return {"status": t.status}


@router.post("/api/admin/tickets/{ticket_id}/close")
def admin_close(ticket_id: int, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    t = db.get(Ticket, ticket_id)
    if t is None:
        raise HTTPException(404, "Обращение не найдено")
    t.status, t.updated_at = "closed", now()
    log_action(db, actor, "ticket.close", "ticket", ticket_id, "")
    db.commit()
    return {"status": t.status}


@router.post("/api/admin/tickets/{ticket_id}/restore-listing")
def admin_restore(ticket_id: int, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """An appeal was right: the listing goes back to moderation (not straight to the site)."""
    t = db.get(Ticket, ticket_id)
    item = db.get(Listing, t.listing_id) if t is not None and t.listing_id else None
    if item is None:
        raise HTTPException(404, "К обращению не привязано объявление")
    if item.status not in {"rejected", "needs_fix", "hidden"}:
        raise HTTPException(409, "Объявление уже в работе")
    item.status, item.note = "pending", ""
    log_action(db, actor, "ticket.restore_listing", "listing", item.id, f"обращение #{ticket_id}")
    db.commit()
    guard.notify_user(db, t.user_tg_id, f"Объявление «{item.title}» возвращено на повторную проверку.", "support")
    return {"listing_id": item.id, "status": item.status}


# ---------- staff: texts, FAQ, switches ----------
@router.get("/api/admin/app-settings")
def admin_settings(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return all_settings(db)


class SettingsIn(BaseModel):
    values: dict[str, str]


@router.put("/api/admin/app-settings")
def admin_save_settings(payload: SettingsIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    unknown = set(payload.values) - set(DEFAULTS)
    if unknown:
        raise HTTPException(400, "Неизвестная настройка")
    for key, value in payload.values.items():
        value = value.strip()[:2000]
        if key in {"krw_rub", "krw_usd"} and value:
            try:
                if float(value.replace(",", ".")) < 0:
                    raise ValueError
            except ValueError:
                raise HTTPException(400, "Курс — число, например 0.065")
        if key == "automod_min_published" and not (value.isdigit() and 1 <= int(value) <= 50):
            raise HTTPException(400, "Число опубликованных объявлений — от 1 до 50")
        if key == "automod_enabled" and value not in {"0", "1"}:
            raise HTTPException(400, "Переключатель — 0 или 1")
        row = db.get(Setting, key) or Setting(key=key)
        row.value = value
        db.add(row)
    log_action(db, actor, "settings.save", "settings", 0, ", ".join(sorted(payload.values)))
    db.commit()
    return all_settings(db)


class FaqIn(BaseModel):
    question: str = Field(min_length=3, max_length=200)
    answer: str = Field(min_length=3, max_length=3000)
    position: int = 0
    active: bool = True


@router.get("/api/admin/faq")
def admin_faq(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return [{"id": r.id, "question": r.question, "answer": r.answer, "position": r.position, "active": r.active}
            for r in db.scalars(select(Faq).order_by(Faq.position, Faq.id)).all()]


@router.post("/api/admin/faq", status_code=201)
def admin_faq_add(payload: FaqIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    row = Faq(**payload.model_dump())
    db.add(row)
    log_action(db, actor, "faq.add", "faq", 0, payload.question[:80])
    db.commit()
    return {"id": row.id}


@router.put("/api/admin/faq/{faq_id}")
def admin_faq_edit(faq_id: int, payload: FaqIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    row = db.get(Faq, faq_id)
    if row is None:
        raise HTTPException(404, "Вопрос не найден")
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    log_action(db, actor, "faq.edit", "faq", faq_id, "")
    db.commit()
    return {"id": row.id}


@router.delete("/api/admin/faq/{faq_id}")
def admin_faq_delete(faq_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    row = db.get(Faq, faq_id)
    if row is not None:
        db.delete(row)
        log_action(db, actor, "faq.delete", "faq", faq_id, "")
        db.commit()
    return {"deleted": True}


# ---------- automatic moderation (off by default) ----------
def maybe_auto_approve(db: Session, item: Listing) -> bool:
    """A listing from a proven author is published at once when the owner switched this on.
    Conditions: no block, no flags (duplicates, same contact), no rejection in the last 60 days, and either the
    «проверенный автор» mark or enough already published listings. Edits of published listings are never automatic."""
    from app import admin_ops as ops
    from app import adminx
    from app.models import User
    import datetime as dt
    if get_setting(db, "automod_enabled") != "1" or item.status != "pending":
        return False
    user = db.get(User, item.author_telegram_id)
    if user is None or user.blocked or user.deleted_at is not None:
        return False
    if adminx.listing_flags(db, item):
        return False
    since = now() - dt.timedelta(days=60)
    bad = db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action.in_(["listing.reject", "listing.needs_fix", "listing.hide"]), AuditLog.object_id == item.id.__str__())) or 0
    rejected_recently = db.scalar(select(func.count()).select_from(Listing).where(
        Listing.author_telegram_id == item.author_telegram_id, Listing.status.in_(["rejected", "needs_fix"]),
        Listing.created_at >= since)) or 0
    if bad or rejected_recently:
        return False
    published = db.scalar(select(func.count()).select_from(Listing).where(
        Listing.author_telegram_id == item.author_telegram_id, Listing.status.in_(["approved", "closed", "sold", "expired"]))) or 0
    need = int(get_setting(db, "automod_min_published") or 3)
    if not (user.trusted or published >= need):
        return False
    ops.moderate_listing(db, "system:auto", item.id, "approve")
    return True


# ---------- staff statistics ----------
@router.get("/api/admin/staff-stats")
def staff_stats(days: int = Query(default=30, ge=1, le=90), actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    import datetime as dt
    since = now() - dt.timedelta(days=days)
    rows = db.execute(select(AuditLog.actor, AuditLog.action, func.count()).where(AuditLog.created_at >= since)
                      .group_by(AuditLog.actor, AuditLog.action)).all()
    per: dict[str, dict] = {}
    for who, action, n in rows:
        d = per.setdefault(who, {"actor": who, "decisions": 0, "replies": 0, "other": 0})
        if action.startswith(("listing.", "resume.", "review.", "report.", "dating.", "employer.")):
            d["decisions"] += n
        elif action.startswith("ticket."):
            d["replies"] += n
        else:
            d["other"] += n
    return sorted(per.values(), key=lambda x: -(x["decisions"] + x["replies"]))
