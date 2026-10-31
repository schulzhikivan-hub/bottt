"""Replies to vacancies ("Откликнуться") and the employer's list of applicants."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app import fields, guard
from app.database import get_db
from app.models import Application, Listing, User
from app.users import writer, get_or_create_user, is_blocked_pair
from app.payments import now, aware

router = APIRouter()
STATUSES = ("new", "viewed", "invited", "rejected")


class ApplyIn(BaseModel):
    message: str = Field(default="", max_length=1000)
    contact: str = Field(min_length=3, max_length=160)


class StatusIn(BaseModel):
    status: Literal["invited", "rejected"]


def _vacancy(db: Session, listing_id: int) -> Listing:
    item = db.get(Listing, listing_id)
    if item is None or item.category != "job" or item.status != "approved":
        raise HTTPException(404, "Вакансия не найдена или уже закрыта")
    if item.expires_at is not None and aware(item.expires_at) <= now():
        raise HTTPException(404, "Вакансия не найдена или уже закрыта")
    return item


@router.post("/api/listings/{listing_id}/apply", status_code=201)
def apply(listing_id: int, payload: ApplyIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    item = _vacancy(db, listing_id)
    if item.author_telegram_id == user_id:
        raise HTTPException(400, "Нельзя откликнуться на свою вакансию")
    if is_blocked_pair(db, user_id, item.author_telegram_id):
        raise HTTPException(403, "Отклик недоступен")
    guard.rate_limit(user_id, "apply", 20, 3600)
    if db.scalar(select(Application.id).where(Application.listing_id == listing_id,
                                              Application.applicant_tg_id == user_id)):
        raise HTTPException(409, "Вы уже откликались на эту вакансию")
    user = get_or_create_user(db, user_id)
    row = Application(listing_id=listing_id, applicant_tg_id=user_id, message=payload.message.strip(),
                      contact=payload.contact.strip(), visa=user.visa or "", city=user.city or "")
    db.add(row)
    db.commit()
    guard.notify_user(db, item.author_telegram_id, f"📩 Новый отклик на вакансию «{item.title}». Откройте «Профиль» → «Отклики».")
    return {"id": row.id, "status": row.status}


@router.get("/api/me/applications")
def my_applications(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    rows = db.execute(select(Application, Listing).join(Listing, Listing.id == Application.listing_id)
                      .where(Application.applicant_tg_id == user_id).order_by(Application.id.desc())).all()
    return [{"id": a.id, "listing_id": l.id, "title": l.title, "city": l.city, "status": a.status,
             "listing_status": l.status, "created_at": a.created_at} for a, l in rows]


@router.get("/api/listings/{listing_id}/applications")
def listing_applications(listing_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    item = db.get(Listing, listing_id)
    if item is None or item.author_telegram_id != user_id:
        raise HTTPException(404, "Вакансия не найдена")
    rows = db.scalars(select(Application).where(Application.listing_id == listing_id)
                      .order_by(Application.id.desc())).all()
    out = [{"id": a.id, "message": a.message, "contact": a.contact, "visa": a.visa, "city": a.city,
            "status": a.status, "created_at": a.created_at} for a in rows]
    for a in rows:  # opening the list marks new ones as seen (the answer shows the state before)
        if a.status == "new":
            a.status = "viewed"
    db.commit()
    return out


@router.put("/api/applications/{app_id}")
def set_status(app_id: int, payload: StatusIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    row = db.get(Application, app_id)
    item = db.get(Listing, row.listing_id) if row else None
    if row is None or item is None or item.author_telegram_id != user_id:
        raise HTTPException(404, "Отклик не найден")
    row.status = payload.status
    db.commit()
    text = {"invited": f"🎉 Вас приглашают по вакансии «{item.title}». Автор свяжется с вами по контакту из отклика.",
            "rejected": f"По вакансии «{item.title}» автор выбрал другого кандидата."}[payload.status]
    guard.notify_user(db, row.applicant_tg_id, text)
    return {"id": row.id, "status": row.status}


def counts(db: Session, listing_ids: list[int]) -> dict[int, dict]:
    """Applicants per vacancy: total and not yet seen (for the author's card)."""
    if not listing_ids:
        return {}
    out: dict[int, dict] = {}
    for lid, status, n in db.execute(select(Application.listing_id, Application.status, func.count())
                                     .where(Application.listing_id.in_(listing_ids))
                                     .group_by(Application.listing_id, Application.status)).all():
        c = out.setdefault(lid, {"total": 0, "new": 0})
        c["total"] += n
        if status == "new":
            c["new"] += n
    return out


def author_card(db: Session, item: Listing) -> dict:
    """What a visitor learns about the author: no Telegram ID, only trust signals."""
    user = db.get(User, item.author_telegram_id)
    active = db.scalar(select(func.count()).select_from(Listing).where(
        Listing.author_telegram_id == item.author_telegram_id, Listing.status == "approved")) or 0
    verified = bool(user and user.employer_status == "approved")
    return {"verified": verified, "company": user.company_name if verified else "",
            "active": active, "since": user.created_at.year if user and user.created_at else None}
