"""Dating (ТЗ § 7.5). Adults only, moderated profiles, no contacts shown, chat only after a mutual like."""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import DatingProfile, DatingLike, Conversation, User, Block
from app.payments import now
from app.users import writer, is_blocked_pair, get_or_create_user
from app.audit import log_action
from app import admin_ops as ops
from app.admin_auth import staff, MOD, ADM, SUP
from app.admin_auth import staff, MOD, ADM, SUP

router = APIRouter()
MIN_AGE = 18
GENDERS = {"f", "m"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024


def upload_root() -> Path:
    root = Path(settings.upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def detect_image(data: bytes) -> str | None:
    """Check the real file type by its first bytes; the client's file name is ignored."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def age_of(birth_year: int) -> int:
    return now().year - birth_year


def _public(profile: DatingProfile) -> dict:
    return {"id": profile.id, "age": age_of(profile.birth_year), "gender": profile.gender,
            "city": profile.city, "bio": profile.bio, "photo_url": f"/api/dating/{profile.id}/photo"}


class DatingIn(BaseModel):
    birth_year: int = Field(ge=1930, le=2100)
    gender: str = Field(min_length=1, max_length=1)
    city: str = Field(min_length=2, max_length=80)
    bio: str = Field(default="", max_length=500)


@router.get("/api/dating/me")
def my_profile(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    profile = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == user_id))
    if profile is None:
        return None
    return {**_public(profile), "status": profile.status, "has_photo": profile.photo_key is not None}


@router.put("/api/dating/profile")
def save_profile(payload: DatingIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    if payload.gender not in GENDERS:
        raise HTTPException(400, "Укажите пол: f или m")
    if age_of(payload.birth_year) < MIN_AGE:
        raise HTTPException(400, "Знакомства доступны только с 18 лет")
    profile = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == user_id))
    if profile is None:
        profile = DatingProfile(owner_tg_id=user_id)
        db.add(profile)
    profile.birth_year = payload.birth_year
    profile.gender = payload.gender
    profile.city = payload.city
    profile.bio = payload.bio
    profile.status = "pending"  # every change goes back to moderation
    db.commit()
    db.refresh(profile)
    return {**_public(profile), "status": profile.status}


@router.post("/api/dating/photo")
async def upload_photo(file: UploadFile = File(...), user_id: str = Depends(writer),
                       db: Session = Depends(get_db)):
    data = await file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Фото больше 5 МБ")
    kind = detect_image(data)
    if kind is None:
        raise HTTPException(400, "Нужна фотография в формате JPEG, PNG или WebP")
    profile = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == user_id))
    if profile is None:
        raise HTTPException(400, "Сначала заполните анкету")
    name = f"{uuid.uuid4().hex}.{kind}"
    (upload_root() / name).write_bytes(data)
    if profile.photo_key:
        old = upload_root() / profile.photo_key
        if old.exists():
            old.unlink()
    profile.photo_key = name
    profile.status = "pending"
    db.commit()
    return {"status": profile.status}


@router.get("/api/dating/{profile_id}/photo")
def profile_photo(profile_id: int, db: Session = Depends(get_db)):
    profile = db.get(DatingProfile, profile_id)
    if profile is None or profile.status != "published" or not profile.photo_key:
        raise HTTPException(404, "Фото не найдено")
    return FileResponse(upload_root() / profile.photo_key)


def _hidden_owners(db: Session, me: str) -> set[str]:
    """Owners I must not see: blocked by me, blocked me, or deleted/blocked accounts."""
    hidden = set(db.scalars(select(Block.blocked_tg_id).where(Block.blocker_tg_id == me)).all())
    hidden |= set(db.scalars(select(Block.blocker_tg_id).where(Block.blocked_tg_id == me)).all())
    hidden |= set(db.scalars(select(User.tg_id).where(
        (User.blocked == True) | (User.deleted_at != None))).all())  # noqa: E711
    return hidden


@router.get("/api/dating")
def browse(gender: str | None = None, city: str | None = None, user_id: str = Depends(writer),
           db: Session = Depends(get_db)):
    hidden = _hidden_owners(db, user_id)
    liked = set(db.scalars(select(DatingLike.to_tg).where(DatingLike.from_tg == user_id)).all())
    stmt = select(DatingProfile).where(DatingProfile.status == "published",
                                       DatingProfile.photo_key != None)  # noqa: E711
    if gender:
        stmt = stmt.where(DatingProfile.gender == gender)
    if city:
        stmt = stmt.where(DatingProfile.city.ilike(f"%{city}%"))
    rows = db.scalars(stmt.order_by(DatingProfile.id.desc()).limit(200)).all()
    return [_public(p) for p in rows
            if p.owner_tg_id != user_id and p.owner_tg_id not in hidden and p.owner_tg_id not in liked][:50]


@router.post("/api/dating/{profile_id}/like")
def like(profile_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    from app.guard import rate_limit
    rate_limit(user_id, "like", 60, 3600)
    target = db.get(DatingProfile, profile_id)
    if target is None or target.status != "published" or target.owner_tg_id == user_id:
        raise HTTPException(404, "Анкета не найдена")
    if is_blocked_pair(db, user_id, target.owner_tg_id):
        raise HTTPException(404, "Анкета не найдена")
    if db.scalar(select(DatingLike).where(DatingLike.from_tg == user_id, DatingLike.to_tg == target.owner_tg_id)) is None:
        db.add(DatingLike(from_tg=user_id, to_tg=target.owner_tg_id))
        db.commit()
    mutual = db.scalar(select(DatingLike).where(DatingLike.from_tg == target.owner_tg_id,
                                                DatingLike.to_tg == user_id))
    if mutual is None:
        return {"match": False}
    a, b = sorted([user_id, target.owner_tg_id])
    key = f"{a}:{b}"
    conv = db.scalar(select(Conversation).where(Conversation.match_key == key))
    if conv is None:
        conv = Conversation(listing_id=None, buyer_tg_id=a, seller_tg_id=b, match_key=key)
        db.add(conv)
        db.commit()
        db.refresh(conv)
    return {"match": True, "conversation_id": conv.id}


# ---- moderation (administrators) ----

def _require_admin(key: str | None):
    if not key or key != settings.admin_api_key:
        raise HTTPException(401, "Admin authentication required")


@router.get("/api/admin/dating")
def admin_dating(status: str = "pending", actor: str = Depends(staff(*MOD)),
                 db: Session = Depends(get_db)):
    rows = db.scalars(select(DatingProfile).where(DatingProfile.status == status)
                      .order_by(DatingProfile.id).limit(100)).all()
    return [{"id": p.id, "owner": p.owner_tg_id, "age": age_of(p.birth_year), "gender": p.gender,
             "city": p.city, "bio": p.bio, "has_photo": p.photo_key is not None, "status": p.status}
            for p in rows]


@router.get("/api/admin/dating/{profile_id}/photo")
def admin_photo(profile_id: int, actor: str = Depends(staff(*MOD)),
                db: Session = Depends(get_db)):
    profile = db.get(DatingProfile, profile_id)
    if profile is None or not profile.photo_key:
        raise HTTPException(404, "Фото не найдено")
    return FileResponse(upload_root() / profile.photo_key)


@router.post("/api/admin/dating/{profile_id}/{decision}")
def decide(profile_id: int, decision: str, actor: str = Depends(staff(*MOD)),
           db: Session = Depends(get_db)):
    try:
        profile = ops.decide_dating(db, actor, profile_id, decision)
    except ops.OpError as exc:
        raise HTTPException(exc.status, exc.message)
    return {"id": profile.id, "status": profile.status}
