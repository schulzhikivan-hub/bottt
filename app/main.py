from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select, func, desc, or_
from sqlalchemy.orm import Session

from app import payments
from app.auth import current_user_id
from app.config import settings
from app.database import Base, engine, get_db, SessionLocal, migrate
from app.models import (
    Listing, Favorite, Conversation, Message, User, Block, Report,
    Pricing, Placement, Payment, Group, Article,
)
from app.schemas import (
    ListingCreate, ListingOut, MessageIn, MessageOut, ConversationOut,
    CATEGORIES, MAX_ACTIVE_PER_CATEGORY,
)
from app.seed import seed_reference
from app.audit import admin_actor, log_action
from app.manage import router as manage_router
from app.users import get_or_create_user, writer, is_blocked_pair
from app.models import DatingProfile
from app.dating import router as dating_router
from app.payments import PaymentError, aware, now

Base.metadata.create_all(bind=engine)
migrate()
with SessionLocal() as _db:
    seed_reference(_db)

app = FastAPI(title="KOREA HUB API", version="0.4.0")
app.include_router(dating_router)
app.include_router(manage_router)
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

REPORT_REASONS = {"fraud", "fake", "spam", "offensive", "inappropriate", "rights", "minor", "profile"}
REPORTS_TO_HIDE = 3  # distinct reporters that hide a listing until a moderator decides
REPORT_TARGETS = {"listing", "user", "message", "dating"}


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC / "admin.html")


@app.get("/health")
def health():
    return {"status": "ok", "service": "korea-hub"}


# ---------- Users, consent, blocks ----------

class ConsentIn(BaseModel):
    accept: bool
    city: str | None = Field(default=None, max_length=80)
    language: str | None = Field(default=None, max_length=8)


@app.get("/api/me")
def me(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_id)
    return {"tg_id": user.tg_id, "consent": user.consent_at is not None, "city": user.city,
            "language": "ru", "blocked": user.blocked, "block_reason": user.block_reason,
            "employer_status": user.employer_status, "company_name": user.company_name}


@app.post("/api/me/consent")
def give_consent(payload: ConsentIn, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    if not payload.accept:
        raise HTTPException(400, "Без согласия пользоваться приложением нельзя")
    user = get_or_create_user(db, user_id)
    if user.deleted_at is not None:
        raise HTTPException(403, "Аккаунт удалён")
    user.consent_at = now()
    if payload.city:
        user.city = payload.city
    if payload.language:
        user.language = payload.language
    db.commit()
    return {"consent": True}


@app.get("/api/me/export")
def export_me(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """Personal data export (ТЗ §4.6)."""
    user = get_or_create_user(db, user_id)
    listings = db.scalars(select(Listing).where(Listing.author_telegram_id == user_id)).all()
    favorites = db.scalars(select(Favorite.listing_id).where(Favorite.user_tg_id == user_id)).all()
    payments_rows = db.scalars(select(Payment).where(Payment.user_tg_id == user_id)).all()
    messages = db.scalars(select(Message).where(Message.sender_tg_id == user_id)).all()
    return {
        "profile": {"tg_id": user.tg_id, "city": user.city, "language": user.language,
                    "consent_at": str(user.consent_at) if user.consent_at else None},
        "listings": [{"id": x.id, "title": x.title, "status": x.status, "category": x.category} for x in listings],
        "favorites": list(favorites),
        "payments": [{"id": x.id, "service": x.service_code, "stars": x.stars, "status": x.status,
                      "paid_at": str(x.paid_at) if x.paid_at else None} for x in payments_rows],
        "messages_sent": [{"text": x.text, "at": str(x.created_at)} for x in messages],
    }


@app.post("/api/me/delete")
def delete_me(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """Account deletion: hides listings, removes favorites and anonymizes own messages (ТЗ §4.6)."""
    user = get_or_create_user(db, user_id)
    user.deleted_at = now()
    user.consent_at = None
    for listing in db.scalars(select(Listing).where(Listing.author_telegram_id == user_id)).all():
        listing.status = "hidden"
    for fav in db.scalars(select(Favorite).where(Favorite.user_tg_id == user_id)).all():
        db.delete(fav)
    for msg in db.scalars(select(Message).where(Message.sender_tg_id == user_id)).all():
        msg.text = "[удалено]"
    db.commit()
    return {"deleted": True}


@app.get("/api/me/blocks")
def my_blocks(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    return db.scalars(select(Block.blocked_tg_id).where(Block.blocker_tg_id == user_id)).all()


@app.post("/api/users/{target}/block")
def block_user(target: str, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    if target == user_id:
        raise HTTPException(400, "Нельзя заблокировать себя")
    exists = db.scalar(select(Block).where(Block.blocker_tg_id == user_id, Block.blocked_tg_id == target))
    if exists is None:
        db.add(Block(blocker_tg_id=user_id, blocked_tg_id=target))
        db.commit()
    return {"blocked": True}


@app.delete("/api/users/{target}/block")
def unblock_user(target: str, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    row = db.scalar(select(Block).where(Block.blocker_tg_id == user_id, Block.blocked_tg_id == target))
    if row is not None:
        db.delete(row)
        db.commit()
    return {"blocked": False}


# ---------- Reports ----------

class ReportIn(BaseModel):
    target_type: str
    target_id: str = Field(min_length=1, max_length=40)
    reason: str
    comment: str = Field(default="", max_length=500)


@app.post("/api/reports", status_code=201)
def create_report(payload: ReportIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    if payload.target_type not in REPORT_TARGETS:
        raise HTTPException(400, "Неизвестный тип жалобы")
    if payload.reason not in REPORT_REASONS:
        raise HTTPException(400, "Неизвестная причина жалобы")
    if payload.target_type == "listing":
        listing = db.get(Listing, int(payload.target_id)) if payload.target_id.isdigit() else None
        if listing is None or listing.author_telegram_id == user_id:
            raise HTTPException(400, "Нельзя пожаловаться на это объявление")
    if payload.target_type == "dating":
        profile = db.get(DatingProfile, int(payload.target_id)) if payload.target_id.isdigit() else None
        if profile is None or profile.owner_tg_id == user_id:
            raise HTTPException(400, "Нельзя пожаловаться на эту анкету")
    if payload.target_type == "user" and payload.target_id == user_id:
        raise HTTPException(400, "Нельзя пожаловаться на себя")
    existing = db.scalar(select(Report).where(
        Report.reporter_tg_id == user_id, Report.target_type == payload.target_type,
        Report.target_id == payload.target_id))
    if existing is None:
        db.add(Report(reporter_tg_id=user_id, target_type=payload.target_type,
                      target_id=payload.target_id, reason=payload.reason, comment=payload.comment))
        db.commit()
    # Several different reporters hide a listing until a moderator decides
    if payload.target_type == "listing":
        count = db.scalar(select(func.count(func.distinct(Report.reporter_tg_id))).where(
            Report.target_type == "listing", Report.target_id == payload.target_id))
        listing = db.get(Listing, int(payload.target_id))
        if count >= REPORTS_TO_HIDE and listing and listing.status == "approved":
            listing.status = "hidden"
            db.commit()
    return {"received": True}


# ---------- Listings ----------

@app.get("/api/listings", response_model=list[ListingOut])
def get_listings(category: str | None = None, city: str | None = None,
                 q: str | None = Query(default=None, max_length=100),
                 db: Session = Depends(get_db)):
    stmt = select(Listing).where(Listing.status == "approved")
    if category:
        if category not in CATEGORIES:
            raise HTTPException(400, "Неизвестная категория")
        stmt = stmt.where(Listing.category == category)
    if city:
        stmt = stmt.where(Listing.city.ilike(f"%{city}%"))
    if q:
        stmt = stmt.where(Listing.title.ilike(f"%{q}%"))
    items = db.scalars(stmt.order_by(Listing.id.desc()).limit(100)).all()
    placed = payments.active_placements(db, [x.id for x in items])
    # Paid placements first: pin > vip > bump > highlight, then newest
    items.sort(key=lambda x: (-payments.KIND_PRIORITY.get(placed.get(x.id, ""), 0), -x.id))
    return items


@app.post("/api/listings", response_model=ListingOut, status_code=201)
def create_listing(payload: ListingCreate, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    if payload.category not in CATEGORIES:
        raise HTTPException(400, "Неизвестная категория")
    active = db.scalar(
        select(func.count()).select_from(Listing).where(
            Listing.author_telegram_id == user_id,
            Listing.category == payload.category,
            Listing.status.in_(["pending", "approved"]),
        )
    )
    if active >= MAX_ACTIVE_PER_CATEGORY:
        raise HTTPException(409, f"Лимит: не больше {MAX_ACTIVE_PER_CATEGORY} активных объявлений в разделе")
    item = Listing(**payload.model_dump(), author_telegram_id=user_id, status="pending")
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@app.get("/api/me/listings", response_model=list[ListingOut])
def my_listings(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    return db.scalars(
        select(Listing).where(Listing.author_telegram_id == user_id).order_by(Listing.id.desc()).limit(100)
    ).all()


# ---------- Favorites ----------

@app.post("/api/favorites/{listing_id}")
def toggle_favorite(listing_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    if db.get(Listing, listing_id) is None:
        raise HTTPException(404, "Объявление не найдено")
    fav = db.scalar(select(Favorite).where(Favorite.user_tg_id == user_id, Favorite.listing_id == listing_id))
    if fav:
        db.delete(fav)
        db.commit()
        return {"favorited": False}
    db.add(Favorite(user_tg_id=user_id, listing_id=listing_id))
    db.commit()
    return {"favorited": True}


@app.get("/api/favorites", response_model=list[ListingOut])
def my_favorites(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    return db.scalars(
        select(Listing)
        .join(Favorite, Favorite.listing_id == Listing.id)
        .where(Favorite.user_tg_id == user_id, Listing.status == "approved")
        .order_by(desc(Favorite.id))
        .limit(100)
    ).all()


# ---------- Chats ----------

def _get_conversation_for(db: Session, conv_id: int, user_id: str) -> Conversation:
    conv = db.get(Conversation, conv_id)
    if conv is None or user_id not in (conv.buyer_tg_id, conv.seller_tg_id):
        # Одинаково отвечаем на «нет» и «чужое», чтобы не раскрывать существование чатов
        raise HTTPException(404, "Чат не найден")
    return conv


@app.post("/api/listings/{listing_id}/conversation")
def start_conversation(listing_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    listing = db.get(Listing, listing_id)
    if listing is None or listing.status != "approved":
        raise HTTPException(404, "Объявление не найдено")
    if listing.author_telegram_id == user_id:
        raise HTTPException(400, "Нельзя написать самому себе")
    if is_blocked_pair(db, user_id, listing.author_telegram_id):
        raise HTTPException(403, "Диалог недоступен")
    conv = db.scalar(select(Conversation).where(
        Conversation.listing_id == listing_id, Conversation.buyer_tg_id == user_id))
    if conv is None:
        conv = Conversation(listing_id=listing_id, buyer_tg_id=user_id,
                            seller_tg_id=listing.author_telegram_id)
        db.add(conv)
        db.commit()
        db.refresh(conv)
    return {"id": conv.id}


@app.get("/api/conversations", response_model=list[ConversationOut])
def my_conversations(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    convs = db.scalars(
        select(Conversation).where(
            (Conversation.buyer_tg_id == user_id) | (Conversation.seller_tg_id == user_id)
        )
    ).all()
    out = []
    for conv in convs:
        other = conv.seller_tg_id if conv.buyer_tg_id == user_id else conv.buyer_tg_id
        if is_blocked_pair(db, user_id, other):
            continue
        listing = db.get(Listing, conv.listing_id) if conv.listing_id else None
        last = db.scalar(select(Message).where(Message.conversation_id == conv.id)
                         .order_by(desc(Message.id)).limit(1))
        out.append(ConversationOut(
            id=conv.id,
            listing_id=conv.listing_id,
            listing_title=("Знакомство" if conv.match_key else
                           (listing.title if listing else "Объявление удалено")),
            last_text=last.text if last else "",
            last_at=last.created_at if last else None,
        ))
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    out.sort(key=lambda c: aware(c.last_at) if c.last_at else epoch, reverse=True)
    return out


@app.get("/api/conversations/{conv_id}/messages", response_model=list[MessageOut])
def get_messages(conv_id: int, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    _get_conversation_for(db, conv_id, user_id)
    msgs = db.scalars(select(Message).where(Message.conversation_id == conv_id)
                      .order_by(desc(Message.id)).limit(100)).all()
    return [MessageOut(id=m.id, mine=m.sender_tg_id == user_id, text=m.text, created_at=m.created_at)
            for m in reversed(msgs)]


@app.post("/api/conversations/{conv_id}/messages", response_model=MessageOut, status_code=201)
def send_message(conv_id: int, payload: MessageIn, user_id: str = Depends(writer),
                 db: Session = Depends(get_db)):
    conv = _get_conversation_for(db, conv_id, user_id)
    other = conv.seller_tg_id if conv.buyer_tg_id == user_id else conv.buyer_tg_id
    if is_blocked_pair(db, user_id, other):
        raise HTTPException(403, "Диалог недоступен")
    text = payload.text.strip()
    if not text:
        raise HTTPException(422, "Пустое сообщение")
    msg = Message(conversation_id=conv_id, sender_tg_id=user_id, text=text)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return MessageOut(id=msg.id, mine=True, text=msg.text, created_at=msg.created_at)


# ---------- Paid services (ТЗ № 2) ----------

@app.get("/api/services")
def list_services(category: str | None = None, db: Session = Depends(get_db)):
    payments.ensure_pricing(db)
    stmt = select(Pricing).where(Pricing.is_active == True)  # noqa: E712
    if category:
        stmt = stmt.where(Pricing.category == category)
    return [{"code": p.code, "category": p.category, "kind": p.kind, "title": p.title,
             "stars": p.stars, "duration_days": p.duration_days}
            for p in db.scalars(stmt.order_by(Pricing.code)).all()]


class InvoiceIn(BaseModel):
    listing_id: int
    service_code: str = Field(min_length=3, max_length=40)


@app.post("/api/payments/invoice")
def create_invoice(payload: InvoiceIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    try:
        payment, url = payments.create_invoice(db, user_id, payload.listing_id, payload.service_code)
    except PaymentError as exc:
        raise HTTPException(400, str(exc))
    return {"payment_id": payment.id, "invoice_url": url, "stars": payment.stars}


@app.get("/api/me/payments")
def my_payments(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    rows = db.scalars(select(Payment).where(Payment.user_tg_id == user_id)
                      .order_by(desc(Payment.id)).limit(100)).all()
    titles = {p.code: p.title for p in db.scalars(select(Pricing)).all()}
    return [{"id": p.id, "service": titles.get(p.service_code, p.service_code), "stars": p.stars,
             "status": p.status, "listing_id": p.listing_id, "charge_id": p.telegram_payment_charge_id,
             "created_at": str(p.created_at), "paid_at": str(p.paid_at) if p.paid_at else None}
            for p in rows]


# ---------- Community and guide ----------

@app.get("/api/groups")
def list_groups(db: Session = Depends(get_db)):
    return [{"id": g.id, "name": g.name, "flag": g.flag, "description": g.description,
             "telegram_link": g.telegram_link, "members_count": g.members_count}
            for g in db.scalars(select(Group).order_by(Group.id)).all()]


@app.get("/api/articles")
def list_articles(section: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Article).where(Article.published == True)  # noqa: E712
    if section:
        stmt = stmt.where(Article.section == section)
    return [{"id": a.id, "section": a.section, "title": a.title,
             "checked_at": str(a.checked_at) if a.checked_at else None}
            for a in db.scalars(stmt.order_by(Article.id)).all()]


@app.get("/api/articles/{article_id}")
def get_article(article_id: int, db: Session = Depends(get_db)):
    a = db.get(Article, article_id)
    if a is None or not a.published:
        raise HTTPException(404, "Статья не найдена")
    return {"id": a.id, "section": a.section, "title": a.title, "body": a.body,
            "source_url": a.source_url, "checked_at": str(a.checked_at) if a.checked_at else None}


# ---------- Admin ----------

def require_admin(x_admin_key: str | None):
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(401, "Admin authentication required")


@app.get("/api/admin/listings", response_model=list[ListingOut])
def pending_listings(actor: str = Depends(admin_actor), db: Session = Depends(get_db)):
    return db.scalars(select(Listing).where(Listing.status == "pending").order_by(Listing.id)).all()


@app.post("/api/admin/listings/{listing_id}/{decision}")
def moderate_listing(listing_id: int, decision: str, reason: str = Query(default="", max_length=300),
                     actor: str = Depends(admin_actor), db: Session = Depends(get_db)):
    """decision: approve | reject (reason required) | needs_fix (comment required)."""
    if decision not in {"approve", "reject", "needs_fix"}:
        raise HTTPException(400, "Решение: approve, reject или needs_fix")
    if decision in {"reject", "needs_fix"} and len(reason.strip()) < 3:
        raise HTTPException(400, "Укажите причину (не короче 3 символов)")
    item = db.get(Listing, listing_id)
    if not item:
        raise HTTPException(404, "Listing not found")
    before = item.status
    item.status = {"approve": "approved", "reject": "rejected", "needs_fix": "needs_fix"}[decision]
    item.note = "" if decision == "approve" else reason.strip()
    log_action(db, actor, f"listing.{decision}", "listing", listing_id, f"{before} -> {item.status}: {reason}")
    db.commit()
    return {"id": item.id, "status": item.status}


@app.get("/api/admin/reports")
def admin_reports(status: str = "new", actor: str = Depends(admin_actor),
                  db: Session = Depends(get_db)):
    rows = db.scalars(select(Report).where(Report.status == status).order_by(Report.id).limit(200)).all()
    return [{"id": r.id, "target_type": r.target_type, "target_id": r.target_id, "reason": r.reason,
             "comment": r.comment, "status": r.status, "created_at": str(r.created_at)} for r in rows]


@app.post("/api/admin/reports/{report_id}/{decision}")
def decide_report(report_id: int, decision: str, actor: str = Depends(admin_actor),
                  db: Session = Depends(get_db)):
    if decision not in {"resolve", "reject"}:
        raise HTTPException(400, "Decision must be resolve or reject")
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "Жалоба не найдена")
    if report.status != "new":
        raise HTTPException(409, "Жалоба уже рассмотрена")
    if decision == "resolve":
        if report.target_type == "listing" and report.target_id.isdigit():
            listing = db.get(Listing, int(report.target_id))
            if listing:
                listing.status = "hidden"
        if report.target_type == "dating" and report.target_id.isdigit():
            profile = db.get(DatingProfile, int(report.target_id))
            if profile:
                profile.status = "rejected"
        if report.target_type == "user":
            user = db.get(User, report.target_id)
            if user:
                user.blocked = True
    report.status = "resolved" if decision == "resolve" else "rejected"
    log_action(db, actor, f"report.{decision}", "report", report_id, report.target_type + ":" + report.target_id)
    db.commit()
    return {"id": report.id, "status": report.status}


@app.get("/api/admin/pricing")
def admin_pricing(actor: str = Depends(admin_actor), db: Session = Depends(get_db)):
    payments.ensure_pricing(db)
    return [{"code": p.code, "category": p.category, "kind": p.kind, "title": p.title,
             "stars": p.stars, "duration_days": p.duration_days, "is_active": p.is_active}
            for p in db.scalars(select(Pricing).order_by(Pricing.code)).all()]


class PricingIn(BaseModel):
    stars: int | None = None
    is_active: bool | None = None


@app.put("/api/admin/pricing/{code}")
def admin_set_price(code: str, payload: PricingIn, actor: str = Depends(admin_actor),
                    db: Session = Depends(get_db)):
    try:
        p = payments.set_price(db, code, payload.stars, payload.is_active)
    except PaymentError as exc:
        raise HTTPException(400, str(exc))
    log_action(db, actor, "pricing.update", "pricing", code, f"stars={payload.stars} active={payload.is_active}")
    db.commit()
    return {"code": p.code, "stars": p.stars, "is_active": p.is_active}


@app.get("/api/admin/payments")
def admin_payments(actor: str = Depends(admin_actor), db: Session = Depends(get_db)):
    rows = db.scalars(select(Payment).order_by(desc(Payment.id)).limit(200)).all()
    return [{"id": p.id, "user": p.user_tg_id, "listing_id": p.listing_id, "service": p.service_code,
             "stars": p.stars, "status": p.status, "charge_id": p.telegram_payment_charge_id,
             "paid_at": str(p.paid_at) if p.paid_at else None} for p in rows]


@app.post("/api/admin/payments/{payment_id}/refund")
def admin_refund(payment_id: int, actor: str = Depends(admin_actor), db: Session = Depends(get_db)):
    try:
        p = payments.refund(db, payment_id)
    except PaymentError as exc:
        raise HTTPException(400, str(exc))
    log_action(db, actor, "payment.refund", "payment", payment_id, p.telegram_payment_charge_id or "")
    db.commit()
    return {"id": p.id, "status": p.status}


@app.get("/api/admin/stats")
def admin_stats(actor: str = Depends(admin_actor), db: Session = Depends(get_db)):
    current = now()
    paid = db.scalars(select(Payment).where(Payment.status == "paid")).all()

    def window(days: int):
        since = current - timedelta(days=days)
        rows = [p for p in paid if p.paid_at and aware(p.paid_at) >= since]
        return {"sales": len(rows), "stars": sum(p.stars for p in rows)}

    by_status = dict(db.execute(select(Listing.status, func.count()).group_by(Listing.status)).all())
    top: dict[str, int] = {}
    for p in paid:
        top[p.service_code] = top.get(p.service_code, 0) + 1
    return {
        "users": db.scalar(select(func.count()).select_from(User)),
        "listings": by_status,
        "reports_new": db.scalar(select(func.count()).select_from(Report).where(Report.status == "new")),
        "payers": len({p.user_tg_id for p in paid}),
        "revenue_stars_total": sum(p.stars for p in paid),
        "day": window(1), "week": window(7), "month": window(30),
        "top_services": sorted(top.items(), key=lambda kv: -kv[1]),
    }


@app.put("/api/admin/groups/{group_id}")
def admin_group(group_id: int, telegram_link: str = "", members_count: int = 0,
                actor: str = Depends(admin_actor), db: Session = Depends(get_db)):
    g = db.get(Group, group_id)
    if g is None:
        raise HTTPException(404, "Группа не найдена")
    if telegram_link:
        if not telegram_link.startswith("https://t.me/"):
            raise HTTPException(400, "Ссылка должна начинаться с https://t.me/")
        g.telegram_link = telegram_link
    g.members_count = max(0, members_count)
    log_action(db, actor, "group.update", "group", group_id, telegram_link)
    db.commit()
    return {"id": g.id, "telegram_link": g.telegram_link}


class ArticleIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(default="", max_length=20000)
    source_url: str = Field(default="", max_length=255)
    checked_at: datetime | None = None
    published: bool = False


@app.put("/api/admin/articles/{article_id}")
def admin_article(article_id: int, payload: ArticleIn, actor: str = Depends(admin_actor),
                  db: Session = Depends(get_db)):
    a = db.get(Article, article_id)
    if a is None:
        raise HTTPException(404, "Статья не найдена")
    if payload.published and (not payload.source_url or payload.checked_at is None):
        raise HTTPException(400, "Для публикации нужны источник и дата проверки")
    a.title, a.body, a.source_url = payload.title, payload.body, payload.source_url
    a.checked_at, a.published = payload.checked_at, payload.published
    log_action(db, actor, "article.update", "article", article_id, f"published={payload.published}")
    db.commit()
    return {"id": a.id, "published": a.published}
