"""Dating (ТЗ § 7.5). Adults only, moderated profiles, no contacts shown, chat only after a mutual like."""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import DatingProfile, DatingLike, Conversation, User, Block
from app.payments import now
from app.users import writer, is_blocked_pair, get_or_create_user
from app.audit import log_action
from app import admin_ops as ops
from app import geo, inbox
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


def age_of_year() -> int:
    return now().year


def age_of(birth_year: int) -> int:
    return now().year - birth_year


def _public(profile: DatingProfile) -> dict:
    return {"id": profile.id, "name": profile.first_name or "", "age": age_of(profile.birth_year), "gender": profile.gender,
            "city": profile.city, "bio": profile.bio, "photo_url": f"/api/dating/{profile.id}/photo",
            "verified": profile.selfie_status == "approved"}


class DatingIn(BaseModel):
    birth_year: int = Field(ge=1930, le=2100)
    gender: str = Field(min_length=1, max_length=1)
    city: str | None = Field(default=None, max_length=100)
    region_code: str | None = Field(default=None, max_length=20)
    district_id: int | None = None
    bio: str = Field(default="", max_length=500)
    first_name: str = Field(default="", max_length=40)


@router.get("/api/dating/me")
def my_profile(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    profile = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == user_id))
    if profile is None:
        return None
    return {**_public(profile), "status": profile.status, "has_photo": profile.photo_key is not None,
            "selfie_status": profile.selfie_status, "paused": profile.paused, "note": profile.note,
            "region_code": profile.region_code, "district_id": profile.district_id}


class PauseIn(BaseModel):
    on: bool


@router.post("/api/dating/pause")
def pause_profile(payload: PauseIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """Hide the profile without deleting it; coming back needs no new moderation."""
    profile = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == user_id))
    if profile is None:
        raise HTTPException(404, "Анкеты нет")
    profile.paused = payload.on
    db.commit()
    return {"paused": profile.paused}


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
    place = geo.resolve_place(db, payload.district_id, payload.region_code, payload.city)
    if place is None:
        raise HTTPException(400, "Выберите город или район из списка. Сайт только для Южной Кореи")
    profile.city, profile.region_code, profile.district_id = place.label, place.region_code, place.district_id
    name = " ".join(payload.first_name.split())
    if inbox.NAME_BAD.search(name):
        raise HTTPException(400, "В имени нельзя писать ссылки, @username и телефон: общение идёт внутри приложения")
    profile.first_name = name
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
    if profile.selfie_key:                      # a new photo must be matched with the selfie again
        profile.selfie_status = "pending"
    db.commit()
    return {"status": profile.status}


@router.post("/api/dating/selfie", status_code=201)
async def upload_selfie(file: UploadFile = File(...), user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """A photo to confirm it is really you (for example with an open palm next to the face). Only moderators see it;
    after approval the profile gets the "verified" mark."""
    data = await file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Фото больше 5 МБ")
    kind = detect_image(data)
    if kind is None:
        raise HTTPException(400, "Нужна фотография в формате JPEG, PNG или WebP")
    profile = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == user_id))
    if profile is None or profile.photo_key is None:
        raise HTTPException(400, "Сначала заполните анкету и загрузите основное фото")
    name = f"selfie-{uuid.uuid4().hex}.{kind}"
    (upload_root() / name).write_bytes(data)
    if profile.selfie_key and (upload_root() / profile.selfie_key).exists():
        (upload_root() / profile.selfie_key).unlink()
    profile.selfie_key, profile.selfie_status = name, "pending"
    db.commit()
    return {"selfie_status": profile.selfie_status}


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
           db: Session = Depends(get_db), age_min: int | None = None, age_max: int | None = None,
           region: str | None = None, active: bool = False):
    hidden = _hidden_owners(db, user_id)
    liked = set(db.scalars(select(DatingLike.to_tg).where(DatingLike.from_tg == user_id)).all())
    stmt = select(DatingProfile).where(DatingProfile.status == "published", DatingProfile.paused.is_(False),
                                       DatingProfile.photo_key != None)  # noqa: E711
    if active:      # was in the app during the last 3 days
        from app.models import User
        from app.payments import now
        import datetime as _dt
        recent = select(User.tg_id).where(User.last_seen_at >= now() - _dt.timedelta(days=3))
        stmt = stmt.where(DatingProfile.owner_tg_id.in_(recent))
    if gender:
        stmt = stmt.where(DatingProfile.gender == gender)
    if city:
        stmt = stmt.where(DatingProfile.city.ilike(f"%{city}%"))
    if region:
        stmt = stmt.where(DatingProfile.region_code == region)
    if age_min is not None:  # age = this year - birth year
        stmt = stmt.where(DatingProfile.birth_year <= age_of_year() - age_min)
    if age_max is not None:
        stmt = stmt.where(DatingProfile.birth_year >= age_of_year() - age_max)
    rows = db.scalars(stmt.order_by(DatingProfile.id.desc()).limit(200)).all()
    return [_public(p) for p in rows
            if p.owner_tg_id != user_id and p.owner_tg_id not in hidden and p.owner_tg_id not in liked][:50]


@router.get("/api/dating/likes-me")
def likes_me(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """Who liked me and has not been answered. Profiles only, never IDs; a like back is the usual like."""
    hidden = _hidden_owners(db, user_id)
    mine_to = set(db.scalars(select(DatingLike.to_tg).where(DatingLike.from_tg == user_id)).all())
    from_ids = [t for t in db.scalars(select(DatingLike.from_tg).where(DatingLike.to_tg == user_id)).all()
                if t not in hidden and t not in mine_to]
    rows = db.scalars(select(DatingProfile).where(DatingProfile.owner_tg_id.in_(from_ids or [""]),
                                                  DatingProfile.status == "published",
                                                  DatingProfile.photo_key != None)  # noqa: E711
                      .order_by(DatingProfile.id.desc())).all()
    return {"count": len(rows), "items": [_public(p) for p in rows]}


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
    cond = DatingProfile.status == status
    if status == "pending":      # a profile that is already public may still wait for its verification
        cond = or_(cond, DatingProfile.selfie_status == "pending")
    rows = db.scalars(select(DatingProfile).where(cond).order_by(DatingProfile.id).limit(100)).all()
    return [{"id": p.id, "owner": p.owner_tg_id, "name": p.first_name or "", "age": age_of(p.birth_year), "gender": p.gender,
             "city": p.city, "bio": p.bio, "has_photo": p.photo_key is not None, "status": p.status,
             "has_selfie": p.selfie_key is not None, "selfie_status": p.selfie_status}
            for p in rows]


@router.get("/api/admin/dating/{profile_id}/selfie")
def admin_selfie(profile_id: int, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    profile = db.get(DatingProfile, profile_id)
    if profile is None or not profile.selfie_key:
        raise HTTPException(404, "Фото-подтверждения нет")
    log_action(db, actor, "dating.view_selfie", "dating", profile_id, "")
    db.commit()
    return FileResponse(upload_root() / profile.selfie_key, headers={"Cache-Control": "no-store"})


@router.get("/api/admin/dating/{profile_id}/photo")
def admin_photo(profile_id: int, actor: str = Depends(staff(*MOD)),
                db: Session = Depends(get_db)):
    profile = db.get(DatingProfile, profile_id)
    if profile is None or not profile.photo_key:
        raise HTTPException(404, "Фото не найдено")
    return FileResponse(upload_root() / profile.photo_key)


@router.post("/api/admin/dating/{profile_id}/{decision}")
def decide(profile_id: int, decision: str, reason: str = Query(default="", max_length=300),
           actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    try:
        profile = ops.decide_dating(db, actor, profile_id, decision)
    except ops.OpError as exc:
        raise HTTPException(exc.status, exc.message)
    if decision == "reject" and reason.strip():
        profile.note = reason.strip()
        db.commit()
    elif decision in {"approve", "verify"}:
        profile.note = ""
        db.commit()
    return {"id": profile.id, "status": profile.status}
