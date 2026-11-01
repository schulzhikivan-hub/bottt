"""Tools for people who run many listings: copy a listing, move it between «me» and «my company»,
company analytics, export of applications, and the company's own activity log."""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import catalog, company
from app.audit import log_action
from app.database import get_db
from app.models import Application, AuditLog, Conversation, Favorite, Listing, Placement, User
from app.payments import aware, now
from app.schemas import ListingOut
from app.users import writer

router = APIRouter()
COPY_FIELDS = ("category", "title", "city", "region_code", "district_id", "price", "description", "contact", "price_amount",
               "housing_provided", "meals_provided", "rooms", "condition", "salary_period", "visas", "employment", "schedule",
               "housing_type", "deposit", "area_m2", "pets_allowed", "furnished", "goods_category", "negotiable", "details")
MAX_DRAFTS = 30


def _manageable(db: Session, listing_id: int, user_id: str) -> Listing:
    item = db.get(Listing, listing_id)
    if item is None or not company.can_manage(db, user_id, item):
        raise HTTPException(404, "Объявление не найдено")
    return item


@router.post("/api/listings/{listing_id}/duplicate", response_model=ListingOut, status_code=201)
def duplicate(listing_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """A copy as a DRAFT (photos are not copied): the person changes what differs and sends it to moderation.
    For a company listing the copy stays the company's."""
    src = _manageable(db, listing_id, user_id)
    drafts = db.scalar(select(func.count()).select_from(Listing).where(
        Listing.author_telegram_id == user_id, Listing.status == "draft")) or 0
    if drafts >= MAX_DRAFTS:
        raise HTTPException(409, f"Черновиков уже {MAX_DRAFTS}: отправьте или удалите часть")
    item = Listing(**{f: getattr(src, f) for f in COPY_FIELDS}, author_telegram_id=user_id, company_id=src.company_id, status="draft")
    db.add(item)
    db.flush()
    log_action(db, user_id, "listing.duplicate", "listing", item.id, f"копия #{src.id}")
    db.commit()
    db.refresh(item)
    return catalog.with_promo(db, [item], owner=True)[0]


class MoveIn(BaseModel):
    company_id: int | None = None          # None = to my personal listings


@router.post("/api/listings/{listing_id}/move", response_model=ListingOut)
def move(listing_id: int, payload: MoveIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """Between «me» and a company I may post for. Paid services running on it must end first (a person's and a company's
    balances are never mixed)."""
    item = _manageable(db, listing_id, user_id)
    if payload.company_id == item.company_id:
        raise HTTPException(409, "Объявление уже там")
    if payload.company_id is None:
        if item.author_telegram_id != user_id:
            raise HTTPException(403, "Вернуть в личные может только тот, кто его подавал")
    else:
        co = db.get(company.Company, payload.company_id)
        if co is None or company.role_of(db, co.id, user_id) not in company.WRITE:
            raise HTTPException(403, "Публиковать от этой компании вам нельзя")
        if co.status != "verified":
            raise HTTPException(409, "Компания ещё не проверена")
        if item.author_telegram_id != user_id and item.company_id is None:
            raise HTTPException(403, "Это чужое объявление")
    running = [p for p in db.scalars(select(Placement).where(Placement.listing_id == item.id, Placement.status == "active")).all()
               if aware(p.ends_at) > now()]
    if running:
        raise HTTPException(409, "На объявлении идёт платное продвижение: перенесите после его окончания")
    before = item.company_id
    item.company_id = payload.company_id
    log_action(db, user_id, "listing.move", "listing", item.id, f"{before or 'лично'} → {payload.company_id or 'лично'}")
    db.commit()
    db.refresh(item)
    return catalog.with_promo(db, [item], owner=True)[0]


# ---------- company: analytics, export, activity ----------
@router.get("/api/company/{company_id}/analytics")
def analytics(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = company._company(db, company_id, user_id)
    rows = db.scalars(select(Listing).where(Listing.company_id == c.id, Listing.status != "archived").order_by(Listing.views.desc()).limit(200)).all()
    ids = [r.id for r in rows] or [0]
    fav = dict(db.execute(select(Favorite.listing_id, func.count()).where(Favorite.listing_id.in_(ids)).group_by(Favorite.listing_id)).all())
    chats = dict(db.execute(select(Conversation.listing_id, func.count()).where(Conversation.listing_id.in_(ids)).group_by(Conversation.listing_id)).all())
    apps = dict(db.execute(select(Application.listing_id, func.count()).where(Application.listing_id.in_(ids), Application.status != "withdrawn")
                           .group_by(Application.listing_id)).all())
    out = [{"id": r.id, "title": r.title, "status": r.status, "views": r.views or 0, "favorites": fav.get(r.id, 0), "chats": chats.get(r.id, 0),
            "applications": apps.get(r.id, 0),
            "conversion": round(100 * (chats.get(r.id, 0) + apps.get(r.id, 0)) / r.views, 1) if r.views else 0.0} for r in rows]
    total = {k: sum(x[k] for x in out) for k in ("views", "favorites", "chats", "applications")}
    best = max(out, key=lambda x: x["views"], default=None)
    return {"items": out, "total": total, "best": best["title"] if best and best["views"] else ""}


@router.get("/api/company/{company_id}/applications.csv")
def applications_csv(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = company._company(db, company_id, user_id, company.TEAM | {"recruiter"})
    listings = {x.id: x.title for x in db.scalars(select(Listing).where(Listing.company_id == c.id)).all()}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Дата", "Вакансия", "Имя", "Контакт", "Виза", "Город", "Статус", "Сообщение", "Ответ"])
    for a in db.scalars(select(Application).where(Application.listing_id.in_(list(listings) or [0]), Application.status != "withdrawn")
                        .order_by(Application.id.desc()).limit(2000)).all():
        w.writerow([a.created_at.date().isoformat(), listings.get(a.listing_id, ""), a.name, a.contact, a.visa, a.city, a.status,
                    a.message.replace("\n", " "), a.reply.replace("\n", " ")])
    log_action(db, user_id, "company.export", "company", c.id, "отклики CSV")
    db.commit()
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="applications-{c.id}.csv"'})


ACTIONS = {"company.edit": "Изменены данные компании", "company.invite": "Создано приглашение", "company.role": "Изменена роль",
           "company.remove": "Участник убран", "company.leave": "Участник вышел", "company.export": "Выгрузка откликов",
           "listing.duplicate": "Создана копия объявления", "listing.move": "Объявление перенесено"}


@router.get("/api/company/{company_id}/activity")
def activity(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = company._company(db, company_id, user_id, company.TEAM)
    lids = [str(i) for i in db.scalars(select(Listing.id).where(Listing.company_id == c.id)).all()]
    rows = db.scalars(select(AuditLog).where(
        ((AuditLog.object_type == "company") & (AuditLog.object_id == str(c.id)) & AuditLog.action.in_(list(ACTIONS))) |
        ((AuditLog.object_type == "listing") & AuditLog.object_id.in_(lids or ["-"]) & AuditLog.action.in_(list(ACTIONS)))
    ).order_by(AuditLog.id.desc()).limit(80)).all()
    names = {u.tg_id: (u.display_name or f"…{u.tg_id[-4:]}") for u in db.scalars(select(User).where(User.tg_id.in_({r.actor for r in rows} or {"-"}))).all()}
    return [{"at": r.created_at.isoformat(), "who": names.get(r.actor, f"…{r.actor[-4:]}"), "what": ACTIONS.get(r.action, r.action),
             "details": r.details, "object": f"{r.object_type} #{r.object_id}"} for r in rows]
