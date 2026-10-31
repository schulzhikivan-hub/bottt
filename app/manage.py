"""Own-listing management, employer application, settings, and administrator user tools."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.audit import log_action
from app.admin_auth import staff, MOD, ADM, SUP
from app.guard import check_text
from app.catalog import drop_irrelevant
from app import admin_ops as ops
from app.database import get_db
from app.models import Listing, User, Block, Report
from app.schemas import CATEGORIES, ListingOut
from app.users import writer, get_or_create_user

router = APIRouter()


def _run(operation, *args):
    try:
        return operation(*args)
    except ops.OpError as exc:
        raise HTTPException(exc.status, exc.message)
EDITABLE = {"pending", "approved", "rejected", "needs_fix"}


def _own_listing(db: Session, listing_id: int, user_id: str) -> Listing:
    item = db.get(Listing, listing_id)
    if item is None or item.author_telegram_id != user_id or item.status == "archived":
        raise HTTPException(404, "Объявление не найдено")
    return item


class ListingEdit(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=160)
    city: str | None = Field(default=None, min_length=2, max_length=80)
    price: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=4000)
    contact: str | None = Field(default=None, max_length=160)
    price_amount: int | None = Field(default=None, ge=0, le=10_000_000_000)
    housing_provided: bool | None = None
    meals_provided: bool | None = None
    rooms: int | None = Field(default=None, ge=0, le=20)
    condition: Literal["new", "used"] | None = None


@router.put("/api/listings/{listing_id}", response_model=ListingOut)
def edit_listing(listing_id: int, payload: ListingEdit, user_id: str = Depends(writer),
                 db: Session = Depends(get_db)):
    item = _own_listing(db, listing_id, user_id)
    if item.status not in EDITABLE:
        raise HTTPException(409, "Это объявление нельзя изменить. Сначала верните его из закрытых")
    changes = drop_irrelevant(item.category, payload.model_dump(exclude_none=True))
    if not changes:
        return item
    check_text(changes.get("title", item.title), changes.get("description", item.description))
    for key, value in changes.items():
        setattr(item, key, value)
    # Любое изменение снова проходит модерацию (ТЗ § 7.4)
    item.status = "pending"
    item.note = ""
    db.commit()
    db.refresh(item)
    return item


def _set_status(db: Session, listing_id: int, user_id: str, allowed: set[str], new_status: str,
                message: str) -> Listing:
    item = _own_listing(db, listing_id, user_id)
    if item.status not in allowed:
        raise HTTPException(409, message)
    item.status = new_status
    db.commit()
    db.refresh(item)
    return item


@router.post("/api/listings/{listing_id}/close", response_model=ListingOut)
def close_listing(listing_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    return _set_status(db, listing_id, user_id, {"approved"}, "closed",
                       "Закрыть можно только опубликованное объявление")


@router.post("/api/listings/{listing_id}/sold", response_model=ListingOut)
def mark_sold(listing_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    item = _own_listing(db, listing_id, user_id)
    if item.category != "marketplace":
        raise HTTPException(400, "Отметка «продано» доступна только в Барахолке")
    return _set_status(db, listing_id, user_id, {"approved"}, "sold",
                       "Отметить продано можно только опубликованный товар")


@router.post("/api/listings/{listing_id}/reopen", response_model=ListingOut)
def reopen_listing(listing_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    # Возврат снова проходит модерацию, как новое объявление
    return _set_status(db, listing_id, user_id, {"closed", "sold"}, "pending",
                       "Вернуть можно только закрытое или проданное объявление")


@router.post("/api/listings/{listing_id}/archive")
def archive_listing(listing_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    item = _own_listing(db, listing_id, user_id)
    item.status = "archived"
    db.commit()
    return {"id": item.id, "status": "archived"}


# ---------- Settings and employer application ----------

class SettingsIn(BaseModel):
    city: str = Field(min_length=2, max_length=80)


class NotificationsIn(BaseModel):
    messages: bool


@router.put("/api/me/notifications")
def update_notifications(payload: NotificationsIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_id)
    user.notify_messages = payload.messages
    db.commit()
    return {"messages": user.notify_messages}


@router.put("/api/me/settings")
def update_settings(payload: SettingsIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_id)
    user.city = payload.city.strip()
    db.commit()
    return {"city": user.city}


class EmployerIn(BaseModel):
    company_name: str = Field(min_length=2, max_length=160)
    registration_no: str = Field(min_length=3, max_length=80)
    contact_person: str = Field(min_length=2, max_length=80)


@router.post("/api/me/employer")
def apply_employer(payload: EmployerIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_id)
    if user.employer_status == "approved":
        raise HTTPException(409, "Статус работодателя уже подтверждён")
    if user.employer_status == "pending":
        raise HTTPException(409, "Заявка уже на проверке")
    user.company_name = payload.company_name.strip()
    user.registration_no = payload.registration_no.strip()
    user.contact_person = payload.contact_person.strip()
    user.employer_status = "pending"
    db.commit()
    return {"employer_status": user.employer_status}


# ---------- Administrator: users, employers, journal ----------

class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.get("/api/admin/users")
def admin_users(q: str = Query(default="", max_length=40), actor: str = Depends(staff(*MOD)),
                db: Session = Depends(get_db)):
    stmt = select(User).order_by(User.created_at.desc()).limit(100)
    if q.strip():
        stmt = select(User).where(User.tg_id.contains(q.strip())).limit(100)
    return [{"tg_id": u.tg_id, "city": u.city, "consent": u.consent_at is not None,
             "blocked": u.blocked, "block_reason": u.block_reason, "deleted": u.deleted_at is not None,
             "employer_status": u.employer_status, "company_name": u.company_name}
            for u in db.scalars(stmt).all()]


@router.post("/api/admin/users/{tg_id}/block")
def admin_block(tg_id: str, payload: ReasonIn, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    _run(ops.block_user, db, actor, tg_id, payload.reason)
    return {"tg_id": tg_id, "blocked": True}


@router.post("/api/admin/users/{tg_id}/unblock")
def admin_unblock(tg_id: str, payload: ReasonIn, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    _run(ops.unblock_user, db, actor, tg_id, payload.reason)
    return {"tg_id": tg_id, "blocked": False}


@router.get("/api/admin/employers")
def admin_employers(status: str = "pending", actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).where(User.employer_status == status).limit(100)).all()
    return [{"tg_id": u.tg_id, "company_name": u.company_name, "registration_no": u.registration_no,
             "contact_person": u.contact_person, "employer_status": u.employer_status} for u in rows]


@router.post("/api/admin/employers/{tg_id}/{decision}")
def admin_employer_decision(tg_id: str, decision: str, actor: str = Depends(staff(*MOD)),
                            db: Session = Depends(get_db)):
    user = _run(ops.decide_employer, db, actor, tg_id, decision)
    return {"tg_id": tg_id, "employer_status": user.employer_status}


@router.get("/api/admin/audit")
def admin_audit(limit: int = Query(default=100, ge=1, le=500), actor: str = Depends(staff(*ADM)),
                db: Session = Depends(get_db)):
    from app.models import AuditLog
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
    return [{"id": r.id, "actor": r.actor, "action": r.action, "object_type": r.object_type,
             "object_id": r.object_id, "details": r.details, "created_at": str(r.created_at)} for r in rows]
