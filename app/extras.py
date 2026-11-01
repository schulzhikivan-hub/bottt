"""Smaller user-facing features: recently viewed, my reviews, listing statistics, price-drop alerts, «typing…»,
chat attachments (PDF and voice), and the unfinished listing form kept on the server."""
import re
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import catalog, guard, photos, reviews
from app.auth import current_user_id
from app.database import get_db
from app.models import (Application, Conversation, Favorite, Listing, Message, Review, TypingMark, User, WizardDraft)
from app.payments import now
from app.users import is_blocked_pair, writer

router = APIRouter()


# ---- recently viewed (the ids live on the phone; the server only returns what is still public) ----
@router.get("/api/listings-batch")
def listings_batch(ids: str = Query(max_length=200), db: Session = Depends(get_db)):
    wanted = [int(x) for x in ids.split(",") if x.strip().isdigit()][:20]
    if not wanted:
        return []
    rows = db.scalars(select(Listing).where(Listing.id.in_(wanted), Listing.status == "approved",
                                            or_(Listing.expires_at.is_(None), Listing.expires_at > now()))).all()
    order = {i: n for n, i in enumerate(wanted)}
    rows = sorted(rows, key=lambda r: order[r.id])
    return catalog.with_promo(db, list(rows))


# ---- my reviews ----
@router.get("/api/me/reviews")
def my_reviews(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    rows = db.scalars(select(Review).where(Review.target_tg_id == user_id, Review.status == "published")
                      .order_by(Review.id.desc()).limit(50)).all()
    items = []
    for r in rows:
        who = db.get(User, r.reviewer_tg_id)
        listing = db.get(Listing, r.listing_id)
        items.append({"id": r.id, "rating": r.rating, "text": r.text, "reply": r.reply,
                      "tags": [reviews.TAGS[t] for t in (r.tags or "").split(",") if t in reviews.TAGS],
                      "name": who.display_name if who and who.display_name else "Пользователь",
                      "listing": listing.title if listing else "", "at": r.created_at.date().isoformat(),
                      "can_reply": not r.reply})
    return {**reviews.summary(db, user_id), "items": items, "tags": reviews.TAGS}


# ---- statistics of my own listing ----
@router.get("/api/listings/{listing_id}/stats")
def listing_stats(listing_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    item = db.get(Listing, listing_id)
    from app import company
    if item is None or not company.can_manage(db, user_id, item, write=False):
        raise HTTPException(404, "Объявление не найдено")
    count = lambda stmt: db.scalar(stmt) or 0   # noqa: E731
    views = item.views or 0
    favorites = count(select(func.count()).select_from(Favorite).where(Favorite.listing_id == listing_id))
    chats = count(select(func.count()).select_from(Conversation).where(Conversation.listing_id == listing_id))
    applications = count(select(func.count()).select_from(Application).where(Application.listing_id == listing_id))
    from app.models import Placement
    from app.payments import aware, now
    promoted = any(aware(pl.ends_at) > now() for pl in db.scalars(select(Placement).where(
        Placement.listing_id == listing_id, Placement.status == "active")).all())
    days_left = max(0, (aware(item.expires_at) - now()).days) if item.expires_at else None
    advice, promote = [], False
    if views < 20 and not promoted:
        advice.append("Мало просмотров: поднятие или VIP покажет объявление выше в ленте."); promote = True
    elif views >= 50 and chats == 0:
        advice.append("Объявление смотрят, но не пишут: проверьте цену, фото и описание.")
    if views >= 20 and favorites and chats == 0:
        advice.append("Его сохраняют в избранное: небольшое снижение цены вернёт этих людей (они получат уведомление).")
    if days_left is not None and days_left <= 3:
        advice.append(f"Срок показа заканчивается через {days_left} дн.: продлите, чтобы не потерять место."); 
    return {"views": views, "favorites": favorites, "chats": chats, "applications": applications,
            "conversion": round(100 * chats / views, 1) if views else 0.0, "days_left": days_left,
            "promoted": promoted, "advice": advice, "promote": promote}


# ---- price drop alert for people who saved the listing ----
def price_dropped(db: Session, item: Listing, old_price: int) -> int:
    fmt = lambda v: f"{v:,}".replace(",", " ") + " ₩"   # noqa: E731
    fans = db.scalars(select(Favorite.user_tg_id).where(Favorite.listing_id == item.id)).all()
    sent = 0
    for tg in fans:
        if tg == item.author_telegram_id:
            continue
        guard.notify_user(db, tg, f"Цена снизилась: «{item.title}» — было {fmt(old_price)}, стало {fmt(item.price_amount)}", "favorites")
        sent += 1
    return sent


# ---- «typing…» ----
@router.post("/api/conversations/{conv_id}/typing")
def typing(conv_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    conv = db.get(Conversation, conv_id)
    if conv is None or user_id not in {conv.buyer_tg_id, conv.seller_tg_id}:
        raise HTTPException(404, "Диалог не найден")
    guard.rate_limit(user_id, "typing", 40, 60)
    mark = db.get(TypingMark, (conv_id, user_id))
    if mark is None:
        db.add(TypingMark(conv_id=conv_id, user_tg_id=user_id, at=now()))
    else:
        mark.at = now()
    db.commit()
    return {"ok": True}


# ---- attachments: PDF and voice messages ----
MAX_FILE = {"pdf": 5 * 1024 * 1024, "audio": 3 * 1024 * 1024}
CONTENT_TYPE = {"pdf": "application/pdf", "audio": "audio/mp4"}


def sniff(data: bytes) -> tuple[str, str] | None:
    """(kind, content-type) judged by the bytes, never by the name the client sends."""
    if data[:5] == b"%PDF-":
        return "pdf", "application/pdf"
    if data[:4] == b"OggS":
        return "audio", "audio/ogg"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "audio", "audio/webm"
    if data[4:8] == b"ftyp":
        return "audio", "audio/mp4"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return "audio", "audio/mpeg"
    return None


@router.post("/api/conversations/{conv_id}/attachment", status_code=201)
async def send_attachment(conv_id: int, file: UploadFile = File(...), user_id: str = Depends(writer), db: Session = Depends(get_db)):
    from app.main import _get_conversation_for, _msg_out
    conv = _get_conversation_for(db, conv_id, user_id)
    other = conv.seller_tg_id if conv.buyer_tg_id == user_id else conv.buyer_tg_id
    other_user = db.get(User, other)
    if is_blocked_pair(db, user_id, other) or (other_user is not None and (other_user.blocked or other_user.deleted_at)):
        raise HTTPException(403, "Диалог недоступен")
    guard.rate_limit(user_id, "chat_file", 15, 600)
    data = await file.read(MAX_FILE["pdf"] + 1)
    kind = sniff(data)
    if kind is None:
        raise HTTPException(400, "Можно отправить PDF или голосовое сообщение")
    if len(data) > MAX_FILE[kind[0]]:
        raise HTTPException(413, "Файл слишком большой (PDF до 5 МБ, голос до 3 МБ)")
    key = uuid.uuid4().hex
    (photos.photo_root() / f"{key}.bin").write_bytes(data)
    name = re.sub(r"[^\w .\-()]", "", file.filename or "")[:60] or ("Голосовое сообщение" if kind[0] == "audio" else "Документ.pdf")
    msg = Message(conversation_id=conv_id, sender_tg_id=user_id, text="", file_key=key, file_name=name, file_kind=kind[0])
    db.add(msg)
    db.commit()
    db.refresh(msg)
    from app.main import message_notice
    guard.notify_user(db, other, message_notice(db, conv, user_id, "🎤 голосовое сообщение" if kind[0] == "audio" else "📎 документ"), "messages")
    return _msg_out(msg, user_id)


@router.get("/api/conversations/{conv_id}/attachment/{key}")
def get_attachment(conv_id: int, key: str, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """Only the two people in the chat can open it. Forced download for documents; nosniff everywhere."""
    from app.main import _get_conversation_for
    _get_conversation_for(db, conv_id, user_id)
    msg = db.scalar(select(Message).where(Message.conversation_id == conv_id, Message.file_key == key))
    path = photos.photo_root() / f"{key}.bin"
    if msg is None or not re.fullmatch(r"[0-9a-f]{32}", key) or not path.exists():
        raise HTTPException(404, "Файл не найден")
    kind = sniff(path.read_bytes()[:16]) or ("pdf", "application/octet-stream")
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    if kind[0] == "pdf":
        headers["Content-Disposition"] = 'attachment; filename="document.pdf"'
    return FileResponse(path, media_type=kind[1], headers=headers)


# ---- the unfinished «new listing» form on the server ----
class DraftIn(BaseModel):
    data: dict
    step: int = 0


@router.get("/api/me/wizard-draft")
def get_draft(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    row = db.get(WizardDraft, user_id)
    return None if row is None else {"data": row.data, "step": row.step, "updated_at": row.updated_at.isoformat()}


@router.put("/api/me/wizard-draft")
def put_draft(payload: DraftIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    import json
    if len(json.dumps(payload.data, ensure_ascii=False)) > 20000:
        raise HTTPException(413, "Черновик слишком большой")
    guard.rate_limit(user_id, "wizard_draft", 120, 3600)
    row = db.get(WizardDraft, user_id) or WizardDraft(user_tg_id=user_id)
    row.data, row.step, row.updated_at = payload.data, max(0, min(payload.step, 10)), now()
    db.add(row)
    db.commit()
    return {"ok": True}


@router.delete("/api/me/wizard-draft")
def delete_draft(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    row = db.get(WizardDraft, user_id)
    if row is not None:
        db.delete(row)
        db.commit()
    return {"deleted": True}
