"""Dating (ТЗ § 7.5). Adults only, moderated profiles, no contacts shown, chat only after a mutual like."""
import datetime as dt
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (Block, Conversation, DatingLike, DatingPrivatePhoto, DatingProfile, DatingView, Message, Notification,
                        User)
from app.payments import aware, now
from app import policy
from app.auth import optional_user_id
from app.fields import VISAS
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


SEEKING = {"", "f", "m", "any"}
GOALS = {"", "friendship", "relationship", "marriage", "chat"}
CANCEL_WINDOW = dt.timedelta(minutes=5)
MAX_AGE = 99


def age_of_year() -> int:
    return now().year


def age_of(birth_year: int) -> int:
    return now().year - birth_year


def age_on(profile: DatingProfile, today: date | None = None) -> int:
    """Exact age from the date of birth; old profiles that only have a year use the year."""
    today = today or now().date()
    d = profile.birth_date
    if d is not None:
        return today.year - d.year - ((today.month, today.day) < (d.month, d.day))
    return today.year - profile.birth_year


def is_invisible(p: DatingProfile, at: dt.datetime | None = None) -> bool:
    """«Невидимка»: the service is paid and switched on. The profile is out of every list but opens by its link."""
    at = at or now()
    return bool(p.invisible_on and p.invisible_until and aware(p.invisible_until) > at)


def vip_kind(p: DatingProfile, at: dt.datetime | None = None) -> str:
    """'' (no VIP), 'vip' (paid, gets a lift) or 'promo' (given free by a promotion: a mark, no lift)."""
    at = at or now()
    if p.vip_until and aware(p.vip_until) > at:
        return "promo" if p.vip_promo else "vip"
    return ""


def counting(p: DatingProfile, at: dt.datetime | None = None) -> bool:
    """Does the VIP clock run? It stops while the profile is paused, invisible, hidden by doubt or in moderation."""
    return p.status == "published" and not p.paused and not p.suspect and not is_invisible(p, at)


def _public(profile: DatingProfile, private: int = 0, at: dt.datetime | None = None) -> dict:
    kind = vip_kind(profile, at)
    return {"id": profile.id, "name": profile.first_name or "", "age": age_on(profile), "gender": profile.gender,
            "age_confirmed": profile.selfie_status == "approved", "city": profile.city, "bio": profile.bio,
            "photo_url": f"/api/dating/{profile.id}/photo", "verified": profile.selfie_status == "approved",
            "seeking": profile.seeking, "goal": profile.goal, "height": profile.height, "visa": profile.visa,
            "vip": bool(kind), "vip_free": kind == "promo", "private_photos": private}


class DatingIn(BaseModel):
    birth_year: int | None = Field(default=None, ge=1930, le=2100)
    birth_date: date | None = None
    gender: str = Field(min_length=1, max_length=1)
    seeking: str = Field(default="", max_length=4)
    goal: str = Field(default="", max_length=12)
    height: int | None = Field(default=None, ge=120, le=230)
    visa: str = Field(default="", max_length=8)
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
    at = now()
    return {**_public(profile, _private_count(db, profile.id), at), "status": profile.status, "has_photo": profile.photo_key is not None,
            "selfie_status": profile.selfie_status, "paused": profile.paused, "note": profile.note,
            "region_code": profile.region_code, "district_id": profile.district_id,
            "birth_date": profile.birth_date.isoformat() if profile.birth_date else None,
            "invisible": is_invisible(profile, at), "invisible_available": bool(profile.invisible_until and aware(profile.invisible_until) > at),
            "invisible_until": profile.invisible_until.isoformat() if profile.invisible_until else None,
            "vip_until": profile.vip_until.isoformat() if profile.vip_until else None,
            "viewers_until": profile.viewers_until.isoformat() if profile.viewers_until else None,
            "super_credits": profile.super_credits, "suspect": profile.suspect}


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
    if payload.birth_date is None and payload.birth_year is None:
        raise HTTPException(400, "Укажите дату рождения")
    if payload.seeking not in SEEKING or payload.goal not in GOALS:
        raise HTTPException(400, "Неверное значение «кого ищу» или «цель»")
    if payload.visa and payload.visa not in VISAS:
        raise HTTPException(400, "Неизвестный тип визы")
    probe = DatingProfile(birth_date=payload.birth_date, birth_year=payload.birth_year or payload.birth_date.year)
    age = age_on(probe)
    if age < MIN_AGE:                    # under 18: the profile is not created at all
        raise HTTPException(400, "Знакомства доступны только с 18 лет")
    if age > MAX_AGE:
        raise HTTPException(400, "Проверьте дату рождения")
    profile = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == user_id))
    if profile is None:
        profile = DatingProfile(owner_tg_id=user_id)
        db.add(profile)
    profile.birth_date = payload.birth_date
    profile.birth_year = payload.birth_year or payload.birth_date.year
    profile.seeking, profile.goal, profile.height, profile.visa = payload.seeking, payload.goal, payload.height, payload.visa
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
    from app import adminplus
    adminplus.notify_staff(db, "Новая анкета знакомств ждёт проверки (админка → Знакомства).", "dating")
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
def profile_photo(profile_id: int, viewer: str | None = Depends(optional_user_id), db: Session = Depends(get_db)):
    profile = db.get(DatingProfile, profile_id)
    if profile is None or not profile.photo_key:
        raise HTTPException(404, "Фото не найдено")
    if viewer != profile.owner_tg_id and not _openable(db, profile, viewer):
        raise HTTPException(404, "Фото не найдено")      # unpublished, paused, doubtful, blocked: not even by the link
    return FileResponse(upload_root() / profile.photo_key)


def _hidden_owners(db: Session, me: str) -> set[str]:
    """Owners I must not see: blocked by me, blocked me, or deleted/blocked accounts."""
    hidden = set(db.scalars(select(Block.blocked_tg_id).where(Block.blocker_tg_id == me)).all())
    hidden |= set(db.scalars(select(Block.blocker_tg_id).where(Block.blocked_tg_id == me)).all())
    hidden |= set(db.scalars(select(User.tg_id).where(
        (User.blocked == True) | (User.deleted_at != None))).all())  # noqa: E711
    return hidden


MODES = {"all", "to_me", "from_me", "mutual"}


def _directions(db: Session, me: str):
    out = {l.to_tg: l for l in db.scalars(select(DatingLike).where(DatingLike.from_tg == me)).all()}
    inc = {l.from_tg: l for l in db.scalars(select(DatingLike).where(DatingLike.to_tg == me)).all()}
    return out, inc, set(out) & set(inc)


def _select(db: Session, me: str, mode: str, f: dict) -> list[DatingProfile]:
    """The profiles of a tab, after all the rules of visibility and the filters. No lift of VIP is applied here."""
    at = now()
    hidden = _hidden_owners(db, me)
    out, inc, mutual = _directions(db, me)
    owners = {"to_me": set(inc) - mutual, "from_me": set(out) - mutual, "mutual": mutual}.get(mode)
    stmt = select(DatingProfile).where(DatingProfile.status == "published", DatingProfile.paused.is_(False),
                                       DatingProfile.suspect.is_(False))
    if f.get("with_photo", True):
        stmt = stmt.where(DatingProfile.photo_key != None)  # noqa: E711
    if owners is not None:
        stmt = stmt.where(DatingProfile.owner_tg_id.in_(owners or [""]))
    if f.get("active"):      # was in the app during the last 3 days
        recent = select(User.tg_id).where(User.last_seen_at >= at - dt.timedelta(days=3))
        stmt = stmt.where(DatingProfile.owner_tg_id.in_(recent))
    if f.get("gender"):
        stmt = stmt.where(DatingProfile.gender == f["gender"])
    if f.get("seeking") in {"f", "m", "any"}:
        stmt = stmt.where(DatingProfile.seeking.in_([f["seeking"], "any"]))
    if f.get("goal"):
        stmt = stmt.where(DatingProfile.goal == f["goal"])
    if f.get("visa"):
        stmt = stmt.where(DatingProfile.visa == f["visa"])
    if f.get("city"):
        stmt = stmt.where(DatingProfile.city.ilike(f"%{f['city']}%"))
    if f.get("region"):
        stmt = stmt.where(DatingProfile.region_code == f["region"])
    if f.get("height_min") is not None:
        stmt = stmt.where(DatingProfile.height >= f["height_min"])
    if f.get("height_max") is not None:
        stmt = stmt.where(DatingProfile.height <= f["height_max"])
    if f.get("verified"):
        stmt = stmt.where(DatingProfile.selfie_status == "approved")
    rows = [p for p in db.scalars(stmt.order_by(DatingProfile.id.desc()).limit(400)).all()
            if p.owner_tg_id != me and p.owner_tg_id not in hidden and not is_invisible(p, at)]
    if mode == "all":
        rows = [p for p in rows if p.owner_tg_id not in out]          # people I already like leave the feed
    ages = (f.get("age_min"), f.get("age_max"))
    if ages[0] is not None or ages[1] is not None:                     # exact age, from the date of birth
        lo, hi = max(MIN_AGE, ages[0] or MIN_AGE), ages[1] if ages[1] is not None else 200
        rows = [p for p in rows if lo <= age_on(p) <= hi]
    if f.get("vip"):                                                    # includes free VIP: the mark is shown for it too
        rows = [p for p in rows if vip_kind(p, at)]
    if f.get("private"):
        counts = _private_counts(db, [p.id for p in rows])
        rows = [p for p in rows if counts.get(p.id)]
    return rows


def _lift(rows: list[DatingProfile], mode: str, inc: dict) -> list[DatingProfile]:
    """VIP stands higher than ordinary profiles, but only within the window rules (app/policy.py).
    «Мне симпатизируют»: super-sympathies first, then newer."""
    at = now()
    if mode == "to_me":
        return sorted(rows, key=lambda p: (0 if inc[p.owner_tg_id].super else 1,
                                           -(aware(inc[p.owner_tg_id].created_at).timestamp() if inc[p.owner_tg_id].created_at else 0)))
    if mode != "all":
        return rows
    rows = sorted(rows, key=lambda p: 0 if vip_kind(p, at) == "vip" else 1)        # stable: the order by date stays inside the groups
    return policy.arrange(rows, lambda p: vip_kind(p, at))


@router.get("/api/dating")
def browse(gender: str | None = None, city: str | None = None, user_id: str = Depends(writer),
           db: Session = Depends(get_db), age_min: int | None = None, age_max: int | None = None,
           region: str | None = None, active: bool = False, mode: str = "all", seeking: str | None = None,
           goal: str | None = None, visa: str | None = None, height_min: int | None = None, height_max: int | None = None,
           verified: bool = False, vip: bool = False, private: bool = False, with_photo: bool = True):
    if mode not in MODES:
        raise HTTPException(400, "Неизвестная вкладка")
    f = dict(gender=gender, city=city, age_min=age_min, age_max=age_max, region=region, active=active, seeking=seeking, goal=goal,
             visa=visa, height_min=height_min, height_max=height_max, verified=verified, vip=vip, private=private, with_photo=with_photo)
    out, inc, mutual = _directions(db, user_id)
    rows = _lift(_select(db, user_id, mode, f), mode, inc)[:50]
    counts = _private_counts(db, [p.id for p in rows])
    at = now()
    items = [_public(p, counts.get(p.id, 0), at) for p in rows]
    if mode == "to_me":
        for p, item in zip(rows, items):
            item["super"] = bool(inc[p.owner_tg_id].super)
    if mode == "mutual":
        for p, item in zip(rows, items):
            a, b = sorted([user_id, p.owner_tg_id])
            conv = db.scalar(select(Conversation).where(Conversation.match_key == f"{a}:{b}"))
            item["conversation_id"] = conv.id if conv else None
    if mode == "from_me":
        for p, item in zip(rows, items):
            made = out[p.owner_tg_id].created_at
            item["can_cancel"] = bool(made and now() - aware(made) <= CANCEL_WINDOW)
    return items


@router.get("/api/dating/counts")
def counts(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """Numbers for the tabs «Мне симпатизируют / Я симпатирую / Взаимные»."""
    return {m: len(_select(db, user_id, m, {})) for m in ("to_me", "from_me", "mutual")}


@router.get("/api/dating/likes-me")
def likes_me(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """Who likes me and has not been answered. Profiles only, never IDs (the same list as the tab «Мне симпатизируют»)."""
    out, inc, mutual = _directions(db, user_id)
    rows = _lift(_select(db, user_id, "to_me", {}), "to_me", inc)
    counts_ = _private_counts(db, [p.id for p in rows])
    return {"count": len(rows), "items": [_public(p, counts_.get(p.id, 0)) for p in rows]}


class LikeIn(BaseModel):
    super: bool = False


@router.post("/api/dating/{profile_id}/like")
def like(profile_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db), body: LikeIn | None = None):
    from app.guard import rate_limit, notify_user
    rate_limit(user_id, "like", 60, 3600)
    mine = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == user_id))
    if mine is not None and (mine.paused or mine.suspect):
        raise HTTPException(403, "Ваша анкета на паузе или на проверке: включите её, чтобы симпатизировать")
    target = db.get(DatingProfile, profile_id)
    if target is None or target.owner_tg_id == user_id or not _openable(db, target, user_id):
        raise HTTPException(404, "Анкета не найдена")
    want_super = bool(body and body.super)
    row = db.scalar(select(DatingLike).where(DatingLike.from_tg == user_id, DatingLike.to_tg == target.owner_tg_id))
    reverse = db.scalar(select(DatingLike).where(DatingLike.from_tg == target.owner_tg_id, DatingLike.to_tg == user_id))
    if row is None:
        if want_super:
            if mine is None or mine.super_credits < 1:
                raise HTTPException(409, "Нет доступных супер-симпатий. Их можно купить в разделе платных услуг")
            mine.super_credits -= 1
        row = DatingLike(from_tg=user_id, to_tg=target.owner_tg_id, created_at=now(), super=want_super)
        db.add(row)
        db.commit()
        if reverse is None:     # the other person is told, without a name or a photo; the notice can be taken back for 5 minutes
            row.notif_id = notify_user(db, target.owner_tg_id,
                                       "⭐ Вам поставили супер-симпатию. Откройте «Знакомства» → «Мне симпатизируют»." if want_super
                                       else "💗 Вам симпатизируют. Откройте «Знакомства» → «Мне симпатизируют».", "dating")
            db.commit()
    if reverse is None:
        return {"mutual": False, "can_cancel_until": (aware(row.created_at) + CANCEL_WINDOW).isoformat() if row.created_at else None}
    a, b = sorted([user_id, target.owner_tg_id])
    key = f"{a}:{b}"
    conv = db.scalar(select(Conversation).where(Conversation.match_key == key))
    if conv is None:
        conv = Conversation(listing_id=None, buyer_tg_id=a, seller_tg_id=b, match_key=key)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        for tg in (user_id, target.owner_tg_id):
            notify_user(db, tg, "💗 Взаимная симпатия! Теперь можно писать друг другу: откройте «Сообщения».", "dating")
    return {"mutual": True, "conversation_id": conv.id}


@router.delete("/api/dating/{profile_id}/like")
def cancel_like(profile_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """Take a sympathy back: possible for 5 minutes, only while it is not mutual. The notice to the other person is withdrawn."""
    target = db.get(DatingProfile, profile_id)
    if target is None:
        raise HTTPException(404, "Анкета не найдена")
    row = db.scalar(select(DatingLike).where(DatingLike.from_tg == user_id, DatingLike.to_tg == target.owner_tg_id))
    if row is None:
        raise HTTPException(404, "Симпатии нет")
    if db.scalar(select(DatingLike.id).where(DatingLike.from_tg == target.owner_tg_id, DatingLike.to_tg == user_id)) is not None:
        raise HTTPException(409, "Симпатия уже взаимная: отменить её нельзя. Чат можно скрыть или заблокировать человека")
    if row.created_at is None or now() - aware(row.created_at) > CANCEL_WINDOW:
        raise HTTPException(409, "Отменить можно только в течение 5 минут")
    if row.notif_id is not None:
        note = db.get(Notification, row.notif_id)
        if note is not None:
            db.delete(note)                      # the notice disappears from the other person's center
    if row.super:
        mine = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == user_id))
        if mine is not None:
            mine.super_credits += 1              # an unused super-sympathy comes back
    db.delete(row)
    db.commit()
    return {"cancelled": True}


class InvisibleIn(BaseModel):
    on: bool


@router.post("/api/dating/invisible")
def set_invisible(payload: InvisibleIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """Switch the paid service «невидимка» on or off while it is paid."""
    p = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == user_id))
    if p is None:
        raise HTTPException(404, "Анкеты нет")
    if payload.on and not (p.invisible_until and aware(p.invisible_until) > now()):
        raise HTTPException(409, "Услуга «Невидимка» не подключена")
    p.invisible_on = payload.on
    db.commit()
    return {"invisible": is_invisible(p)}


def erase_profile(db: Session, user_id: str) -> None:
    """Delete a profile with everything that belongs to it: sympathies (sent and received), views, private photos, files.
    The chats of people who liked each other stay with their history (they are not part of the profile)."""
    p = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == user_id))
    if p is None:
        return
    for row in db.scalars(select(DatingLike).where(or_(DatingLike.from_tg == user_id, DatingLike.to_tg == user_id))).all():
        if row.notif_id is not None:
            note = db.get(Notification, row.notif_id)
            if note is not None:
                db.delete(note)
        db.delete(row)
    for v in db.scalars(select(DatingView).where(DatingView.profile_id == p.id)).all():
        db.delete(v)
    for ph in db.scalars(select(DatingPrivatePhoto).where(DatingPrivatePhoto.profile_id == p.id)).all():
        (upload_root() / ph.key).unlink(missing_ok=True)
        db.delete(ph)
    for key in (p.photo_key, p.selfie_key):
        if key:
            (upload_root() / key).unlink(missing_ok=True)
    db.delete(p)
    db.commit()


@router.delete("/api/dating/profile")
def delete_profile(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    erase_profile(db, user_id)
    return {"deleted": True}


@router.get("/api/dating/{profile_id}")
def profile_detail(profile_id: int, viewer: str = Depends(writer), db: Session = Depends(get_db)):
    """One profile, also by a direct link. A paused, doubtful or unpublished one is «not found», an invisible one opens."""
    p = db.get(DatingProfile, profile_id)
    if p is None:
        raise HTTPException(404, "Анкета не найдена")
    own = p.owner_tg_id == viewer
    if not own and not _openable(db, p, viewer):
        raise HTTPException(404, "Анкета не найдена")
    if not own:        # a view is recorded once a day per person (shown to the owner only with the paid service)
        day = now() - dt.timedelta(days=1)
        if db.scalar(select(DatingView.id).where(DatingView.profile_id == p.id, DatingView.viewer_tg == viewer, DatingView.at > day)) is None:
            db.add(DatingView(profile_id=p.id, viewer_tg=viewer))
            db.commit()
    out, inc, mutual = _directions(db, p.owner_tg_id if own else viewer)
    item = _public(p, _private_count(db, p.id))
    if not own:
        mine_like = db.scalar(select(DatingLike).where(DatingLike.from_tg == viewer, DatingLike.to_tg == p.owner_tg_id))
        item.update(liked=mine_like is not None, liked_me=p.owner_tg_id in inc_of(db, viewer), mutual=p.owner_tg_id in mutual)
    return item


def inc_of(db: Session, me: str) -> set[str]:
    return set(db.scalars(select(DatingLike.from_tg).where(DatingLike.to_tg == me)).all())


@router.get("/api/admin/dating-stats")
def dating_stats(actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """For the admin panel: how many sympathies, how many are mutual, how many mutual pairs actually started to talk."""
    total = db.scalar(select(func.count()).select_from(DatingLike)) or 0
    supers = db.scalar(select(func.count()).select_from(DatingLike).where(DatingLike.super.is_(True))) or 0
    pairs = db.scalars(select(Conversation).where(Conversation.match_key.is_not(None))).all()
    talked = sum(1 for c in pairs if db.scalar(select(func.count()).select_from(Message).where(
        Message.conversation_id == c.id, Message.sender_tg_id == c.buyer_tg_id)) and db.scalar(
        select(func.count()).select_from(Message).where(Message.conversation_id == c.id, Message.sender_tg_id == c.seller_tg_id)))
    return {"sympathies": total, "super": supers, "mutual_pairs": len(pairs), "talking_pairs": talked,
            "chat_conversion_pct": round(100 * talked / len(pairs)) if pairs else None,
            "mutual_pct": round(100 * 2 * len(pairs) / total) if total else None}


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
        if decision == "approve":
            profile.suspect = False             # the doubt is cleared by a moderator
        db.commit()
    return {"id": profile.id, "status": profile.status}
