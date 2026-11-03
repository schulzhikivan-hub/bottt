"""Tools for people who run many listings: copy a listing, move it between «me» and «my company»,
company analytics, export of applications, and the company's own activity log."""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
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
           "company.remove": "Участник убран", "company.leave": "Участник вышел", "company.export": "Выгрузка откликов", "company.stage": "Этап отклика изменён",
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


# ---------- quick price change and the author's history ----------
class PriceIn(BaseModel):
    price_amount: int = Field(ge=0, le=10_000_000_000)


MAX_PRICE_SHIFT = 0.5          # more than ±50% goes through moderation like any other big edit
MAX_PRICE_CHANGES_PER_DAY = 3


@router.post("/api/listings/{listing_id}/price", response_model=ListingOut)
def quick_price(listing_id: int, payload: PriceIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """A small price change without moderation: people who saved the listing are told about a cheaper price."""
    item = _manageable(db, listing_id, user_id)
    if item.status != "approved":
        raise HTTPException(409, "Быстро менять цену можно у опубликованного объявления")
    if item.pending_changes:
        raise HTTPException(409, "Правка уже ждёт проверки: цена подтвердится вместе с ней")
    old, new = item.price_amount, payload.price_amount
    if not old:
        raise HTTPException(409, "У объявления не указана цена: добавьте её через «Изменить»")
    if new == old:
        raise HTTPException(409, "Цена та же")
    if abs(new - old) / old > MAX_PRICE_SHIFT:
        raise HTTPException(409, "Изменение больше 50%: используйте «Изменить», объявление проверит модератор")
    today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    done = db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "listing.price", AuditLog.object_id == str(item.id), AuditLog.created_at >= today)) or 0
    if done >= MAX_PRICE_CHANGES_PER_DAY:
        raise HTTPException(429, "Сегодня цену уже меняли три раза. Попробуйте завтра")
    snapshot(db, item, "Перед сменой цены", user_id)
    item.price_amount = new
    log_action(db, user_id, "listing.price", "listing", item.id, f"{old} → {new}")
    db.commit()
    if new < old:
        from app import extras
        extras.price_dropped(db, item, old)
        db.commit()
    db.refresh(item)
    return catalog.with_promo(db, [item], owner=True)[0]


HISTORY_LABELS = {"listing.price": "Изменена цена", "listing.duplicate": "Создана копия", "listing.move": "Объявление перенесено",
                  "listing.approve": "Опубликовано модератором", "listing.reject": "Отклонено модератором", "listing.needs_fix": "Возвращено на доработку",
                  "listing.hide": "Скрыто модератором", "listing.show": "Возвращено модератором", "listing.admin_edit": "Поправлено модератором",
                  "listing.undo": "Решение модератора отменено"}
SHOW_DETAILS = {"listing.price", "listing.duplicate", "listing.move", "listing.reject", "listing.needs_fix"}


@router.get("/api/listings/{listing_id}/history")
def history(listing_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """What happened to the listing, newest first (for its author or team)."""
    item = _manageable(db, listing_id, user_id)
    events = [{"at": item.created_at.isoformat(), "what": "Объявление создано", "details": ""}]
    if item.published_at:
        events.append({"at": item.published_at.isoformat(), "what": "Начало показа", "details": ""})
    for r in db.scalars(select(AuditLog).where(AuditLog.object_type == "listing", AuditLog.object_id == str(item.id),
                                               AuditLog.action.in_(list(HISTORY_LABELS))).order_by(AuditLog.id)).all():
        events.append({"at": r.created_at.isoformat(), "what": HISTORY_LABELS[r.action], "details": r.details if r.action in SHOW_DETAILS else ""})
    for v in db.scalars(select(ListingVersion).where(ListingVersion.listing_id == item.id).order_by(ListingVersion.id)).all():
        events.append({"at": v.at.isoformat(), "what": "Версия до изменения", "details": v.why, "version_id": v.id,
                       "preview": f"{v.data.get('title', '')} · {v.data.get('price_amount') or ''}"})
    events.sort(key=lambda e: e["at"], reverse=True)
    return events


@router.get("/api/me/unread")
def unread_messages(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """How many messages wait for me (for the badge on «Сообщения»); a person's own side and the company side they work on."""
    from app.models import Message
    team = select(company.CompanyMember.company_id).where(company.CompanyMember.tg_id == user_id, company.CompanyMember.role.in_(company.WRITE))
    mine = select(Conversation.id).where((Conversation.buyer_tg_id == user_id) | (Conversation.seller_tg_id == user_id) |
                                         Conversation.listing_id.in_(select(Listing.id).where(Listing.company_id.in_(team))))
    from app.models import ConvHide
    rows = db.execute(select(Message.conversation_id, func.count(), func.max(Message.created_at)).where(
        Message.conversation_id.in_(mine), Message.sender_tg_id != user_id, Message.read_at.is_(None)).group_by(Message.conversation_id)).all()
    hides = {h.conv_id: h.at for h in db.scalars(select(ConvHide).where(ConvHide.user_tg_id == user_id)).all()}
    n = sum(c for cid, c, last in rows if cid not in hides or aware(last) > aware(hides[cid]))      # a chat I hid does not light up
    return {"messages": int(n)}


# ---------- versions: what the listing looked like before an edit, and a way back ----------
from sqlalchemy import JSON, DateTime, Integer, String          # noqa: E402
from sqlalchemy.orm import Mapped, mapped_column                # noqa: E402

from app.database import Base                                   # noqa: E402

VERSION_FIELDS = ("title", "description", "contact", "price", "price_amount", "city", "region_code", "district_id", "salary_period",
                  "visas", "employment", "schedule", "housing_provided", "meals_provided", "rooms", "condition", "housing_type", "deposit",
                  "area_m2", "pets_allowed", "furnished", "goods_category", "negotiable", "details")
MAX_VERSIONS = 15


class ListingVersion(Base):
    __tablename__ = "listing_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(Integer, index=True)
    at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
    by: Mapped[str] = mapped_column(String(40), default="")
    why: Mapped[str] = mapped_column(String(60), default="")
    data: Mapped[dict] = mapped_column(JSON)


def snapshot(db: Session, item: Listing, why: str, by: str) -> None:
    """Remember the listing as it is NOW (call before changing it). Only the last MAX_VERSIONS are kept."""
    db.add(ListingVersion(listing_id=item.id, by=by, why=why, data={f: getattr(item, f) for f in VERSION_FIELDS}))
    db.flush()
    old = db.scalars(select(ListingVersion.id).where(ListingVersion.listing_id == item.id).order_by(ListingVersion.id.desc()).offset(MAX_VERSIONS)).all()
    if old:
        db.execute(delete(ListingVersion).where(ListingVersion.id.in_(old)))


@router.post("/api/listings/{listing_id}/restore/{version_id}", response_model=ListingOut)
def restore(listing_id: int, version_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """Bring back an older version. It goes through the same rules as any edit: small things at once, text and price via moderation."""
    from app import lifecycle
    from app.manage import EDITABLE
    item = _manageable(db, listing_id, user_id)
    v = db.get(ListingVersion, version_id)
    if v is None or v.listing_id != item.id:
        raise HTTPException(404, "Версия не найдена")
    if item.status not in EDITABLE:
        raise HTTPException(409, "Это объявление сейчас нельзя изменить")
    changes = {f: val for f, val in v.data.items() if getattr(item, f) != val}
    if not changes and item.pending_changes:           # the old version is what is published now: «return» means to withdraw the waiting edit
        item.pending_changes = None
        item.has_edit = False
        log_action(db, user_id, "listing.restore", "listing", item.id, f"отозвана правка, версия #{version_id}")
        db.commit()
        db.refresh(item)
        return catalog.with_promo(db, [item], owner=True)[0]
    if not changes:
        raise HTTPException(409, "Это уже текущая версия")
    snapshot(db, item, "Перед возвратом версии", user_id)
    lifecycle.edit(db, item, changes)
    log_action(db, user_id, "listing.restore", "listing", item.id, f"версия #{version_id}")
    db.commit()
    db.refresh(item)
    return catalog.with_promo(db, [item], owner=True)[0]


# ---------- price hint: what similar listings cost ----------
@router.get("/api/price-hint")
def price_hint(category: str, region_code: str | None = None, employment: str | None = None, housing_type: str | None = None,
               goods_category: str | None = None, db: Session = Depends(get_db)):
    """The middle of the prices of similar published listings (a quarter cheaper, the median, a quarter more expensive).
    Narrow search first (same region and kind); if there are too few, the whole category."""
    def prices(*extra):
        stmt = select(Listing.price_amount).where(Listing.status == "approved", Listing.category == category, Listing.price_amount.is_not(None),
                                                  Listing.price_amount > 0, *extra)
        return sorted(db.scalars(stmt.limit(500)).all())
    narrow = []
    if region_code:
        narrow.append(Listing.region_code == region_code)
    if category == "job" and employment:
        narrow.append(Listing.employment == employment)
    if category == "housing" and housing_type:
        narrow.append(Listing.housing_type == housing_type)
    if category == "marketplace" and goods_category:
        narrow.append(Listing.goods_category == goods_category)
    rows, scope = (prices(*narrow), "похожим в вашем регионе") if narrow else ([], "")
    if len(rows) < 5:
        rows, scope = prices(), "объявлениям этого раздела"
    if len(rows) < 5:
        return {"n": len(rows), "hint": None}
    q = lambda p: rows[min(len(rows) - 1, int(p * (len(rows) - 1) + 0.5))]   # noqa: E731
    return {"n": len(rows), "scope": scope, "hint": {"low": q(0.25), "median": q(0.5), "high": q(0.75)}}
