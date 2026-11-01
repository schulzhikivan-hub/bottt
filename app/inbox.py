"""Own account: in-app notification feed, notification settings by kind, display name, avatar, "last seen"."""
import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app import guard, photos
from app.config import settings
from app.auth import current_user_id
from app.database import get_db
from app.guard import NOTIFY_KINDS
from app.models import Notification, User
from app.payments import now, aware
from app.users import writer, get_or_create_user

router = APIRouter()
NAME_BAD = re.compile(r"(https?://|www\.|@|t\.me|\+?\d[\d\s().-]{7,})", re.I)
AVATAR_SIDE = 256


def touch(db: Session, user_id: str) -> None:
    """Remember that the person was here (at most once a minute: one write per request would be wasteful)."""
    user = db.get(User, user_id)
    if user is None:
        return
    seen = user.last_seen_at
    if seen is None or (now() - aware(seen)).total_seconds() > 60:
        user.last_seen_at = now()
        db.commit()


def coarse_status(user: User | None) -> str:
    """Only a rough idea, never the exact time: online (5 min), recently (a day), otherwise nothing."""
    if user is None or user.last_seen_at is None:
        return ""
    age = now() - aware(user.last_seen_at)
    if age <= timedelta(minutes=5):
        return "online"
    if age <= timedelta(days=1):
        return "recently"
    return ""


def unread_count(db: Session, user_id: str) -> int:
    return db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_tg_id == user_id, Notification.read_at.is_(None))) or 0


# ---------- feed ----------

@router.get("/api/me/notifications")
def feed(limit: int = 50, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    rows = db.scalars(select(Notification).where(Notification.user_tg_id == user_id)
                      .order_by(Notification.id.desc()).limit(max(1, min(limit, 100)))).all()
    return {"unread": unread_count(db, user_id),
            "items": [{"id": n.id, "kind": n.kind, "label": NOTIFY_KINDS.get(n.kind, "KOREA HUB"), "text": n.text,
                       "created_at": n.created_at, "read": n.read_at is not None} for n in rows]}


class ReadIn(BaseModel):
    ids: list[int] | None = None  # empty = all


@router.post("/api/me/notifications/read")
def mark_read(payload: ReadIn, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    stmt = select(Notification).where(Notification.user_tg_id == user_id, Notification.read_at.is_(None))
    if payload.ids:
        stmt = stmt.where(Notification.id.in_(payload.ids))
    for n in db.scalars(stmt).all():
        n.read_at = now()
    db.commit()
    return {"unread": unread_count(db, user_id)}


# ---------- settings by kind ----------

class PrefsIn(BaseModel):
    master: bool | None = None
    kinds: dict[str, bool] | None = None
    mode: str | None = None          # now | hourly | daily: how often non-urgent notices reach Telegram
    digest: bool | None = None       # new matches for saved searches once a day instead of at once


def prefs_view(user: User) -> dict:
    prefs = user.notify_prefs or {}
    return {"master": bool(user.notify_messages), "digest": prefs.get("searches_digest") is True,
            "mode": guard.digest_mode(user), "modes": [{"key": k, "label": v} for k, v in guard.DIGEST_MODES.items()],
            "quiet_hours": settings.quiet_hours, "critical": sorted(guard.CRITICAL_KINDS),
            "kinds": [{"key": k, "label": label, "on": prefs.get(k, True) is not False} for k, label in NOTIFY_KINDS.items()]}


@router.get("/api/me/notify-prefs")
def get_prefs(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    return prefs_view(get_or_create_user(db, user_id))


@router.put("/api/me/notify-prefs")
def set_prefs(payload: PrefsIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_id)
    if payload.master is not None:
        user.notify_messages = payload.master
    if payload.kinds:
        unknown = set(payload.kinds) - set(NOTIFY_KINDS)
        if unknown:
            raise HTTPException(422, "Неизвестный вид уведомлений")
        user.notify_prefs = {**(user.notify_prefs or {}), **payload.kinds}
    if payload.mode is not None:
        if payload.mode not in guard.DIGEST_MODES:
            raise HTTPException(422, "Режим: now, hourly или daily")
        user.notify_prefs = {**(user.notify_prefs or {}), "_digest": payload.mode}
    if payload.digest is not None:
        user.notify_prefs = {**(user.notify_prefs or {}), "searches_digest": payload.digest}
    db.commit()
    return prefs_view(user)


# ---------- name and avatar ----------

class ProfileIn(BaseModel):
    display_name: str = Field(max_length=40)


@router.put("/api/me/profile")
def set_profile(payload: ProfileIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    name = " ".join(payload.display_name.split())
    if NAME_BAD.search(name):
        raise HTTPException(400, "В имени нельзя писать ссылки, @username и телефон: контакты указывают в объявлении")
    user = get_or_create_user(db, user_id)
    user.display_name = name
    db.commit()
    return {"display_name": user.display_name}


@router.post("/api/me/avatar", status_code=201)
async def set_avatar(file: UploadFile = File(...), user_id: str = Depends(writer), db: Session = Depends(get_db)):
    data = await file.read(photos.MAX_UPLOAD_BYTES + 1)
    if len(data) > photos.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Фото больше 8 МБ")
    full, _thumb = photos.process_image(data)
    import io
    img = ImageOps.fit(Image.open(io.BytesIO(full)).convert("RGB"), (AVATAR_SIDE, AVATAR_SIDE))  # centred square
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85, optimize=True)
    key = uuid.uuid4().hex
    (photos.photo_root() / f"{key}.jpg").write_bytes(out.getvalue())
    user = get_or_create_user(db, user_id)
    if user.avatar_key:
        photos.delete_files(user.avatar_key)
    user.avatar_key = key
    db.commit()
    return {"avatar": photos.url(key)}


@router.delete("/api/me/avatar")
def remove_avatar(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_id)
    if user.avatar_key:
        photos.delete_files(user.avatar_key)
        user.avatar_key = None
        db.commit()
    return {"avatar": None}
