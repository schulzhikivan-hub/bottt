from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.config import settings
from app.database import Base, engine, get_db
from app.models import Listing, Favorite, Conversation, Message
from app.schemas import (
    ListingCreate, ListingOut, MessageIn, MessageOut, ConversationOut,
    CATEGORIES, MAX_ACTIVE_PER_CATEGORY,
)

Base.metadata.create_all(bind=engine)
app = FastAPI(title="KOREA HUB API", version="0.2.0")
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC / "admin.html")


@app.get("/health")
def health():
    return {"status": "ok", "service": "korea-hub"}


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
    return db.scalars(stmt.order_by(Listing.id.desc()).limit(100)).all()


@app.post("/api/listings", response_model=ListingOut, status_code=201)
def create_listing(payload: ListingCreate, user_id: str = Depends(current_user_id),
                   db: Session = Depends(get_db)):
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
def toggle_favorite(listing_id: int, user_id: str = Depends(current_user_id),
                    db: Session = Depends(get_db)):
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
def start_conversation(listing_id: int, user_id: str = Depends(current_user_id),
                       db: Session = Depends(get_db)):
    listing = db.get(Listing, listing_id)
    if listing is None or listing.status != "approved":
        raise HTTPException(404, "Объявление не найдено")
    if listing.author_telegram_id == user_id:
        raise HTTPException(400, "Нельзя написать самому себе")
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
        listing = db.get(Listing, conv.listing_id)
        last = db.scalar(select(Message).where(Message.conversation_id == conv.id)
                         .order_by(desc(Message.id)).limit(1))
        out.append(ConversationOut(
            id=conv.id,
            listing_id=conv.listing_id,
            listing_title=listing.title if listing else "Объявление удалено",
            last_text=last.text if last else "",
            last_at=last.created_at if last else None,
        ))
    out.sort(key=lambda c: c.last_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out


@app.get("/api/conversations/{conv_id}/messages", response_model=list[MessageOut])
def get_messages(conv_id: int, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    _get_conversation_for(db, conv_id, user_id)
    msgs = db.scalars(select(Message).where(Message.conversation_id == conv_id)
                      .order_by(desc(Message.id)).limit(100)).all()
    return [MessageOut(id=m.id, mine=m.sender_tg_id == user_id, text=m.text, created_at=m.created_at)
            for m in reversed(msgs)]


@app.post("/api/conversations/{conv_id}/messages", response_model=MessageOut, status_code=201)
def send_message(conv_id: int, payload: MessageIn, user_id: str = Depends(current_user_id),
                 db: Session = Depends(get_db)):
    _get_conversation_for(db, conv_id, user_id)
    text = payload.text.strip()
    if not text:
        raise HTTPException(422, "Пустое сообщение")
    msg = Message(conversation_id=conv_id, sender_tg_id=user_id, text=text)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return MessageOut(id=msg.id, mine=True, text=msg.text, created_at=msg.created_at)


# ---------- Admin ----------

def require_admin(x_admin_key: str | None):
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(401, "Admin authentication required")


@app.get("/api/admin/listings", response_model=list[ListingOut])
def pending_listings(x_admin_key: str | None = Header(default=None), db: Session = Depends(get_db)):
    require_admin(x_admin_key)
    return db.scalars(select(Listing).where(Listing.status == "pending").order_by(Listing.id)).all()


@app.post("/api/admin/listings/{listing_id}/{decision}")
def moderate_listing(listing_id: int, decision: str, x_admin_key: str | None = Header(default=None),
                     db: Session = Depends(get_db)):
    require_admin(x_admin_key)
    if decision not in {"approve", "reject"}:
        raise HTTPException(400, "Decision must be approve or reject")
    item = db.get(Listing, listing_id)
    if not item:
        raise HTTPException(404, "Listing not found")
    item.status = "approved" if decision == "approve" else "rejected"
    db.commit()
    return {"id": item.id, "status": item.status}
