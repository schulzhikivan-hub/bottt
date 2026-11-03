"""One place to manage listings and dating profiles: filtered lists, full editing, history.

Listings: a list with filters (section, status, author, flags), the full edit of every field (the same fields the author can change, with the
same checks), the history of the object (journal, complaints, paid services). Dating profiles: a list with filters, edit of the text
fields, removal of the photo, deletion of the profile with a reason the person sees."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import catalog, dating, geo, guard, manage
from app.admin_auth import MOD, staff
from app.audit import log_action
from app.database import get_db
from app.models import (AuditLog, DatingLike, DatingProfile, Listing, Payment, Placement, Report, User)
from app.payments import aware, now

router = APIRouter()


# ---------- listings ----------
@router.put("/api/admin/listings/{listing_id}/full")
def edit_full(listing_id: int, payload: manage.ListingEdit, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """The moderator changes any field of the listing. It applies at once (the moderator is the decision), the search text is rebuilt,
    and every changed field goes to the journal as «было → стало»."""
    item = db.get(Listing, listing_id)
    if item is None:
        raise HTTPException(404, "Объявление не найдено")
    changes = catalog.drop_irrelevant(item.category, payload.model_dump(exclude_none=True))
    if not changes:
        return {"changed": []}
    guard.check_text(changes.get("title", item.title), changes.get("description", item.description))
    if any(k in changes for k in ("city", "region_code", "district_id")):         # a place is picked from the list, like the author does
        place = geo.resolve_place(db, changes.pop("district_id", None), changes.pop("region_code", None), changes.pop("city", None))
        if place is None:
            raise HTTPException(400, "Место нужно выбрать из списка (регион или район Кореи)")
        changes.update(city=place.label, region_code=place.region_code, district_id=place.district_id)
    was = {}
    for key, value in changes.items():
        if key == "details":
            merged = {**(item.details or {}), **value}
            if merged != (item.details or {}):
                was[key] = ("…", "…")
                item.details = merged
            continue
        if isinstance(value, str):
            value = value.strip()
        if getattr(item, key, None) != value:
            was[key] = (getattr(item, key, None), value)
            setattr(item, key, value)
    if not was:
        return {"changed": []}
    item.search_text = " ".join([item.title or "", item.description or "", item.city or ""]).lower()
    log_action(db, actor, "listing.admin_edit", "listing", listing_id, "; ".join(f"{k}: {str(a)[:40]} → {str(b)[:40]}" for k, (a, b) in was.items()))
    db.commit()
    guard.notify_user(db, item.author_telegram_id, f"Модератор поправил ваше объявление «{item.title}»: {', '.join(was)}.", "moderation")
    return {"changed": list(was)}


@router.get("/api/admin/listings/{listing_id}/history")
def history(listing_id: int, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    item = db.get(Listing, listing_id)
    if item is None:
        raise HTTPException(404, "Объявление не найдено")
    journal = db.scalars(select(AuditLog).where(AuditLog.object_type == "listing", AuditLog.object_id == str(listing_id)).order_by(AuditLog.id.desc()).limit(40)).all()
    reports = db.scalars(select(Report).where(Report.target_type == "listing", Report.target_id == str(listing_id)).order_by(Report.id.desc()).limit(20)).all()
    places = db.scalars(select(Placement).where(Placement.listing_id == listing_id).order_by(Placement.id.desc()).limit(10)).all()
    return {"journal": [{"at": r.created_at.isoformat(), "who": r.actor, "action": r.action, "details": r.details} for r in journal],
            "reports": [{"at": r.created_at.isoformat(), "reason": r.reason, "comment": r.comment, "status": r.status} for r in reports],
            "services": [{"kind": p.kind, "status": p.status, "from": p.starts_at.isoformat(), "to": p.ends_at.isoformat(), "free": bool(p.promo)} for p in places],
            "flags": {"quarantine": item.quarantine, "dispute": item.dispute, "has_edit": item.has_edit, "company_id": item.company_id}}


# ---------- dating profiles ----------
def _profile_row(db: Session, p: DatingProfile) -> dict:
    at = now()
    inc = db.scalar(select(func.count()).select_from(DatingLike).where(DatingLike.to_tg == p.owner_tg_id)) or 0
    out = db.scalar(select(func.count()).select_from(DatingLike).where(DatingLike.from_tg == p.owner_tg_id)) or 0
    return {"id": p.id, "owner": p.owner_tg_id, "name": p.first_name or "", "age": dating.age_on(p), "gender": p.gender, "city": p.city, "bio": p.bio,
            "status": p.status, "selfie_status": p.selfie_status, "paused": p.paused, "suspect": p.suspect, "has_photo": p.photo_key is not None,
            "has_selfie": p.selfie_key is not None, "seeking": p.seeking, "goal": p.goal, "height": p.height, "visa": p.visa, "note": p.note,
            "vip": bool(dating.vip_kind(p, at)), "invisible": dating.is_invisible(p, at), "likes_in": inc, "likes_out": out,
            "created_at": p.created_at.isoformat() if p.created_at else None}


@router.get("/api/admin/dating-list")
def dating_list(status: str = "", gender: str = "", q: str = Query(default="", max_length=40), flag: str = "", limit: int = Query(default=30, ge=1, le=100),
                offset: int = Query(default=0, ge=0, le=10000), actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """flag: selfie (waiting for verification) · paused · suspect · vip · invisible."""
    stmt = select(DatingProfile)
    if status:
        stmt = stmt.where(DatingProfile.status == status)
    if gender in {"f", "m"}:
        stmt = stmt.where(DatingProfile.gender == gender)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(DatingProfile.first_name.ilike(like), DatingProfile.city.ilike(like), DatingProfile.bio.ilike(like), DatingProfile.owner_tg_id.contains(q.strip())))
    if flag == "selfie":
        stmt = stmt.where(DatingProfile.selfie_status == "pending")
    elif flag == "paused":
        stmt = stmt.where(DatingProfile.paused.is_(True))
    elif flag == "suspect":
        stmt = stmt.where(DatingProfile.suspect.is_(True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(DatingProfile.id.desc()).limit(limit).offset(offset)).all()
    items = [_profile_row(db, p) for p in rows]
    if flag == "vip":
        items = [i for i in items if i["vip"]]
    elif flag == "invisible":
        items = [i for i in items if i["invisible"]]
    return {"total": total, "items": items}


class DatingEdit(BaseModel):
    first_name: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=100)
    bio: str | None = Field(default=None, max_length=500)
    seeking: str | None = Field(default=None, pattern="^(|f|m|any)$")
    goal: str | None = Field(default=None, pattern="^(|friendship|relationship|marriage|chat)$")
    height: int | None = Field(default=None, ge=120, le=230)
    visa: str | None = Field(default=None, max_length=8)
    paused: bool | None = None
    suspect: bool | None = None
    reason: str = Field(default="", max_length=200)


@router.put("/api/admin/dating/{profile_id}/edit")
def dating_edit(profile_id: int, p: DatingEdit, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    prof = db.get(DatingProfile, profile_id)
    if prof is None:
        raise HTTPException(404, "Анкета не найдена")
    was = {}
    for key, value in p.model_dump(exclude={"reason"}, exclude_none=True).items():
        if isinstance(value, str):
            value = value.strip()
        if key in {"bio", "first_name"}:
            guard.check_text(value if key == "first_name" else "", value if key == "bio" else "")
        if getattr(prof, key) != value:
            was[key] = (getattr(prof, key), value)
            setattr(prof, key, value)
    if not was:
        return {"changed": []}
    log_action(db, actor, "dating.admin_edit", "dating", profile_id, "; ".join(f"{k}: {str(a)[:30]} → {str(b)[:30]}" for k, (a, b) in was.items()) + (f" ({p.reason})" if p.reason else ""))
    db.commit()
    guard.notify_user(db, prof.owner_tg_id, "Модератор поправил вашу анкету" + (f": {p.reason}" if p.reason else "") + ".", "moderation")
    return {"changed": list(was)}


class DatingDelete(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@router.delete("/api/admin/dating/{profile_id}/photo")
def dating_photo_remove(profile_id: int, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """Take the photo away (it breaks the rules). A published profile without a photo goes back to moderation."""
    prof = db.get(DatingProfile, profile_id)
    if prof is None or not prof.photo_key:
        raise HTTPException(404, "Фото нет")
    (dating.upload_root() / prof.photo_key).unlink(missing_ok=True)
    prof.photo_key = None
    if prof.status == "published":
        prof.status = "pending"
    log_action(db, actor, "dating.photo_remove", "dating", profile_id, "")
    db.commit()
    guard.notify_user(db, prof.owner_tg_id, "Фото вашей анкеты убрано модератором: загрузите другое, и анкета снова пройдёт проверку.", "moderation")
    return {"removed": True}


@router.post("/api/admin/dating/{profile_id}/delete")
def dating_delete(profile_id: int, p: DatingDelete, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    prof = db.get(DatingProfile, profile_id)
    if prof is None:
        raise HTTPException(404, "Анкета не найдена")
    owner = prof.owner_tg_id
    dating.erase_profile(db, owner)
    log_action(db, actor, "dating.admin_delete", "dating", profile_id, p.reason)
    db.commit()
    guard.notify_user(db, owner, f"Ваша анкета удалена модератором: {p.reason.strip()}", "moderation")
    return {"deleted": True}
