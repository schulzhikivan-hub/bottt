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
    Pricing, Placement, Payment, Group, Article, ListingPhoto,
)
from app.schemas import (
    ListingCreate, ListingOut, MessageIn, MessageOut, ConversationOut,
    CATEGORIES, MAX_ACTIVE_PER_CATEGORY,
)
from app.seed import seed_reference
from app.admin_auth import router as admin_auth_router, bootstrap_admins
from app.webauth import router as webauth_router
from app.audit import log_action
from app.admin_auth import staff, MOD, ADM, SUP
from app.manage import router as manage_router
from app.guard import rate_limit, check_text, duplicate_note, notify_user
from app import admin_ops as ops
from app import catalog
from app import photos, applications, searches
from app import geo
from app import fields as listing_fields
from app.users import get_or_create_user, writer, is_blocked_pair
from app.models import DatingProfile
from app.dating import router as dating_router
from app.payments import PaymentError, aware, now

Base.metadata.create_all(bind=engine)
migrate()
with SessionLocal() as _db:
    seed_reference(_db)
    bootstrap_admins(_db)
    geo.backfill_places(_db)
    catalog.backfill_search_fields(_db)

app = FastAPI(title="KOREA HUB API", version="0.4.0")
app.include_router(dating_router)
app.include_router(manage_router)
app.include_router(admin_auth_router)
app.include_router(webauth_router)
app.include_router(catalog.router)
app.include_router(photos.router)
app.include_router(applications.router)
app.include_router(searches.router)
app.include_router(geo.router)
app.include_router(listing_fields.router)
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

def _run(operation, *args):
    """Run a shared admin action and turn its error into an HTTP error."""
    try:
        return operation(*args)
    except ops.OpError as exc:
        raise HTTPException(exc.status, exc.message)


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
    city: str | None = Field(default=None, max_length=100)
    district_id: int | None = None
    region_code: str | None = Field(default=None, max_length=20)
    language: str | None = Field(default=None, max_length=8)


@app.get("/api/me")
def me(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_id)
    return {"tg_id": user.tg_id, "consent": user.consent_at is not None, "city": user.city,
            "region_code": user.region_code, "district_id": user.district_id,
            "language": "ru", "blocked": user.blocked, "block_reason": user.block_reason,
            "employer_status": user.employer_status, "company_name": user.company_name, "visa": user.visa,
            "notify_messages": user.notify_messages}


@app.post("/api/me/consent")
def give_consent(payload: ConsentIn, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    if not payload.accept:
        raise HTTPException(400, "Без согласия пользоваться приложением нельзя")
    user = get_or_create_user(db, user_id)
    if user.deleted_at is not None:
        raise HTTPException(403, "Аккаунт удалён")
    user.consent_at = now()
    place = geo.resolve_place(db, payload.district_id, payload.region_code, payload.city)
    if place:
        user.city, user.region_code, user.district_id = place.label, place.region_code, place.district_id
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
        for photo in db.scalars(select(ListingPhoto).where(ListingPhoto.listing_id == listing.id)).all():
            photos.delete_files(photo.key)  # the author's photos are removed with the account
            db.delete(photo)
    for fav in db.scalars(select(Favorite).where(Favorite.user_tg_id == user_id)).all():
        db.delete(fav)
    for msg in db.scalars(select(Message).where(Message.sender_tg_id == user_id)).all():
        msg.text = "[удалено]"
    db.commit()
    return {"deleted": True}


@app.get("/api/me/blocks")
def my_blocks(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """Who I blocked. Only a number of the entry is returned, never the other person's Telegram ID."""
    rows = db.scalars(select(Block).where(Block.blocker_tg_id == user_id).order_by(Block.id)).all()
    return [{"id": b.id, "label": f"Пользователь №{i}"} for i, b in enumerate(rows, 1)]


@app.delete("/api/me/blocks/{block_id}")
def unblock_by_entry(block_id: int, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    row = db.get(Block, block_id)
    if row is None or row.blocker_tg_id != user_id:
        raise HTTPException(404, "Запись не найдена")
    db.delete(row)
    db.commit()
    return {"blocked": False}


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
    rate_limit(user_id, "report", 10, 3600)
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
            listing.note = "Скрыто по жалобам пользователей, пока модератор проверяет"
            db.commit()
            notify_user(db, listing.author_telegram_id,
                        f"Объявление «{listing.title}» временно скрыто: на него пожаловались несколько человек. Модератор проверит его.")
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
    rate_limit(user_id, "listing", 10, 3600)
    check_text(payload.title, payload.description)
    place = geo.resolve_place(db, payload.district_id, payload.region_code, payload.city)
    if place is None:
        raise HTTPException(400, "Выберите город или район из списка. Сайт только для Южной Кореи")
    active = db.scalar(
        select(func.count()).select_from(Listing).where(
            Listing.author_telegram_id == user_id,
            Listing.category == payload.category,
            Listing.status.in_(["draft", "pending", "approved"]),
        )
    )
    if active >= MAX_ACTIVE_PER_CATEGORY:
        raise HTTPException(409, f"Лимит: не больше {MAX_ACTIVE_PER_CATEGORY} активных объявлений в разделе")
    data = catalog.clean_extras(payload.category, payload.model_dump(exclude={"city", "region_code", "district_id", "draft"}))
    item = Listing(**data, city=place.label, region_code=place.region_code, district_id=place.district_id,
                   author_telegram_id=user_id, status="draft" if payload.draft else "pending")
    db.add(item)
    db.commit()
    db.refresh(item)
    dup = duplicate_note(db, user_id, payload.title, payload.description, item.id)
    if dup:
        # Для модератора: виден в журнале, автор этого не видит
        log_action(db, "system", "listing.duplicate_suspected", "listing", item.id, dup)
        db.commit()
    return item


@app.get("/api/me/listings", response_model=list[ListingOut])
def my_listings(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Listing).where(Listing.author_telegram_id == user_id).order_by(Listing.id.desc()).limit(100)
    ).all()
    return catalog.with_promo(db, list(rows), owner=True)


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
    rows = db.scalars(
        select(Listing)
        .join(Favorite, Favorite.listing_id == Listing.id)
        .where(Favorite.user_tg_id == user_id, Listing.status == "approved")
        .order_by(desc(Favorite.id))
        .limit(100)
    ).all()
    return catalog.with_promo(db, list(rows))


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
def get_messages(conv_id: int, before_id: int | None = Query(default=None, ge=1),
                 limit: int = Query(default=100, ge=1, le=100),
                 user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """Latest messages; pass before_id (the oldest id on screen) to load earlier ones."""
    _get_conversation_for(db, conv_id, user_id)
    stmt = select(Message).where(Message.conversation_id == conv_id)
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    msgs = db.scalars(stmt.order_by(desc(Message.id)).limit(limit)).all()
    return [MessageOut(id=m.id, mine=m.sender_tg_id == user_id, text=m.text, created_at=m.created_at)
            for m in reversed(msgs)]


@app.post("/api/conversations/{conv_id}/messages", response_model=MessageOut, status_code=201)
def send_message(conv_id: int, payload: MessageIn, user_id: str = Depends(writer),
                 db: Session = Depends(get_db)):
    conv = _get_conversation_for(db, conv_id, user_id)
    other = conv.seller_tg_id if conv.buyer_tg_id == user_id else conv.buyer_tg_id
    other_user = db.get(User, other)
    if is_blocked_pair(db, user_id, other) or (other_user is not None and (other_user.blocked or other_user.deleted_at)):
        raise HTTPException(403, "Диалог недоступен")
    text = payload.text.strip()
    if not text:
        raise HTTPException(422, "Пустое сообщение")
    rate_limit(user_id, "message", 30, 60)
    msg = Message(conversation_id=conv_id, sender_tg_id=user_id, text=text)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    notify_user(db, other, f"Новое сообщение в KOREA HUB:\n{text[:200]}")
    return MessageOut(id=msg.id, mine=True, text=msg.text, created_at=msg.created_at)


def _other_party(conv: Conversation, user_id: str) -> str:
    return conv.seller_tg_id if conv.buyer_tg_id == user_id else conv.buyer_tg_id


@app.post("/api/conversations/{conv_id}/block")
def block_from_chat(conv_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """Block the other person without the client ever learning their Telegram ID."""
    conv = _get_conversation_for(db, conv_id, user_id)
    return block_user(_other_party(conv, user_id), user_id, db)


class ChatReportIn(BaseModel):
    reason: str
    comment: str = Field(default="", max_length=500)


@app.post("/api/conversations/{conv_id}/report", status_code=201)
def report_from_chat(conv_id: int, payload: ChatReportIn, user_id: str = Depends(writer),
                     db: Session = Depends(get_db)):
    conv = _get_conversation_for(db, conv_id, user_id)
    return create_report(ReportIn(target_type="user", target_id=_other_party(conv, user_id),
                                  reason=payload.reason, comment=payload.comment), user_id, db)


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

@app.get("/api/admin/listings")
def pending_listings(actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """New listings and waiting edits. For an edit, pending_changes shows what would change."""
    rows = list(db.scalars(select(Listing).where(ops.queue_condition()).order_by(Listing.id)).all())
    shown = catalog.with_promo(db, rows, owner=True)
    return [{**o.model_dump(mode="json"), "pending_changes": x.pending_changes, "author_id": x.author_telegram_id}
            for o, x in zip(shown, rows)]


@app.get("/api/admin/listings/{listing_id}")
def admin_listing(listing_id: int, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    item = db.get(Listing, listing_id)
    if item is None:
        raise HTTPException(404, "Объявление не найдено")
    shown = catalog.with_promo(db, [item], owner=True)[0]
    return {**shown.model_dump(mode="json"), "pending_changes": item.pending_changes, "author_id": item.author_telegram_id}


@app.post("/api/admin/listings/{listing_id}/{decision}")
def moderate_listing(listing_id: int, decision: str, reason: str = Query(default="", max_length=300),
                     actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """decision: approve | reject (reason required) | needs_fix (comment required)."""
    item = _run(ops.moderate_listing, db, actor, listing_id, decision, reason)
    return {"id": item.id, "status": item.status}


@app.get("/api/admin/reports")
def admin_reports(status: str = "new", actor: str = Depends(staff(*MOD)),
                  db: Session = Depends(get_db)):
    rows = db.scalars(select(Report).where(Report.status == status).order_by(Report.id).limit(200)).all()
    return [{"id": r.id, "target_type": r.target_type, "target_id": r.target_id, "reason": r.reason,
             "comment": r.comment, "status": r.status, "created_at": str(r.created_at)} for r in rows]


@app.post("/api/admin/reports/{report_id}/{decision}")
def decide_report(report_id: int, decision: str, actor: str = Depends(staff(*MOD)),
                  db: Session = Depends(get_db)):
    report = _run(ops.decide_report, db, actor, report_id, decision)
    return {"id": report.id, "status": report.status}


@app.get("/api/admin/pricing")
def admin_pricing(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    payments.ensure_pricing(db)
    return [{"code": p.code, "category": p.category, "kind": p.kind, "title": p.title,
             "stars": p.stars, "duration_days": p.duration_days, "is_active": p.is_active}
            for p in db.scalars(select(Pricing).order_by(Pricing.code)).all()]


class PricingIn(BaseModel):
    stars: int | None = None
    is_active: bool | None = None


@app.put("/api/admin/pricing/{code}")
def admin_set_price(code: str, payload: PricingIn, actor: str = Depends(staff(*ADM)),
                    db: Session = Depends(get_db)):
    try:
        p = payments.set_price(db, code, payload.stars, payload.is_active)
    except PaymentError as exc:
        raise HTTPException(400, str(exc))
    log_action(db, actor, "pricing.update", "pricing", code, f"stars={payload.stars} active={payload.is_active}")
    db.commit()
    return {"code": p.code, "stars": p.stars, "is_active": p.is_active}


@app.get("/api/admin/payments")
def admin_payments(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    rows = db.scalars(select(Payment).order_by(desc(Payment.id)).limit(200)).all()
    return [{"id": p.id, "user": p.user_tg_id, "listing_id": p.listing_id, "service": p.service_code,
             "stars": p.stars, "status": p.status, "charge_id": p.telegram_payment_charge_id,
             "paid_at": str(p.paid_at) if p.paid_at else None} for p in rows]


@app.post("/api/admin/payments/{payment_id}/refund")
def admin_refund(payment_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    try:
        p = payments.refund(db, payment_id)
    except PaymentError as exc:
        raise HTTPException(400, str(exc))
    log_action(db, actor, "payment.refund", "payment", payment_id, p.telegram_payment_charge_id or "")
    db.commit()
    return {"id": p.id, "status": p.status}


@app.get("/api/admin/stats")
def admin_stats(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return ops.compute_stats(db)


@app.put("/api/admin/groups/{group_id}")
def admin_group(group_id: int, telegram_link: str = "", members_count: int = 0,
                actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
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
def admin_article(article_id: int, payload: ArticleIn, actor: str = Depends(staff(*ADM)),
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
