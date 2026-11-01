"""What the panel shows and does with people, payments and listings: lists with names (not bare IDs), filters and paging,
editing a person, a preview of a listing the way people see it, reasons attached to decisions."""
import datetime as dt
import html

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import geo, guard, inbox, photos, site
from app.admin_auth import ADM, MOD, SUPPORT, staff
from app.audit import log_action
from app.database import get_db
from app.fields import VISAS
from app.models import Listing, Payment, Pricing, Report, User
from app.payments import aware, now

router = APIRouter()

STATUS_RU = {"draft": "черновик", "pending": "на проверке", "approved": "опубликовано", "rejected": "отклонено", "needs_fix": "на доработке",
             "hidden": "скрыто", "closed": "закрыто автором", "sold": "продано", "expired": "срок истёк", "archived": "удалено автором"}


def names(db: Session, ids) -> dict[str, str]:
    ids = {str(i) for i in ids if i}
    if not ids:
        return {}
    return {u.tg_id: u.display_name or "" for u in db.scalars(select(User).where(User.tg_id.in_(ids))).all()}


# ---------- people ----------
@router.get("/api/admin/users-list")
def users_list(q: str = Query(default="", max_length=40), flt: str = Query(default="", max_length=12),
               sort: str = Query(default="new", max_length=8), offset: int = Query(default=0, ge=0, le=100000),
               limit: int = Query(default=30, ge=1, le=100), actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    """Everyone, newest first. flt: blocked | employers | trusted | new7 | deleted | resume. q: ID, name or city."""
    cond = []
    q = q.strip()
    if q:
        like = f"%{q}%"
        cond.append(or_(User.tg_id.contains(q), User.display_name.ilike(like), User.city.ilike(like), User.email.ilike(like)))
    week = now() - dt.timedelta(days=7)
    if flt == "blocked":
        cond.append(User.blocked.is_(True))
    elif flt == "employers":
        cond.append(User.employer_status == "approved")
    elif flt == "trusted":
        cond.append(User.trusted.is_(True))
    elif flt == "new7":
        cond.append(User.created_at >= week)
    elif flt == "deleted":
        cond.append(User.deleted_at.is_not(None))
    total = db.scalar(select(func.count()).select_from(User).where(*cond)) or 0
    order = User.last_seen_at.desc().nulls_last() if sort == "seen" else User.created_at.desc()
    rows = db.scalars(select(User).where(*cond).order_by(order, User.tg_id).limit(limit).offset(offset)).all()
    ids = [u.tg_id for u in rows]
    ads = dict(db.execute(select(Listing.author_telegram_id, func.count()).where(Listing.author_telegram_id.in_(ids))
                          .group_by(Listing.author_telegram_id)).all()) if ids else {}
    reps = dict(db.execute(select(Report.target_id, func.count()).where(Report.target_type == "user", Report.target_id.in_(ids))
                           .group_by(Report.target_id)).all()) if ids else {}
    return {"total": total, "items": [{
        "tg_id": u.tg_id, "name": u.display_name, "city": u.city, "blocked": u.blocked, "block_reason": u.block_reason,
        "blocked_until": u.blocked_until.isoformat() if u.blocked_until else None, "trusted": u.trusted, "deleted": u.deleted_at is not None,
        "employer_status": u.employer_status, "company": u.company_name, "consent": u.consent_at is not None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_seen_at": u.last_seen_at.isoformat() if u.last_seen_at else None,
        "listings": ads.get(u.tg_id, 0), "reports_against": reps.get(u.tg_id, 0), "balance": u.balance_stars or 0} for u in rows]}


class UserEditIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=40)
    visa: str | None = Field(default=None, max_length=8)
    region_code: str | None = Field(default=None, max_length=20)
    district_id: int | None = None
    company_name: str | None = Field(default=None, max_length=160)
    clear_avatar: bool = False


@router.put("/api/admin/users/{tg_id}/edit")
def edit_user(tg_id: str, payload: UserEditIn, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """Fix a profile: an unsuitable name, a wrong city or visa, a picture that breaks the rules. The person is told."""
    user = db.get(User, tg_id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    changes: list[str] = []
    if payload.display_name is not None:
        name = " ".join(payload.display_name.split())
        if name and inbox.NAME_BAD.search(name):
            raise HTTPException(400, "В имени нельзя писать ссылки, @username и телефон")
        if name != user.display_name:
            changes.append(f"имя: «{user.display_name}» → «{name}»")
            user.display_name = name
    if payload.visa is not None:
        if payload.visa and payload.visa not in VISAS:
            raise HTTPException(400, "Неизвестный тип визы")
        if payload.visa != user.visa:
            changes.append(f"виза: {user.visa or '—'} → {payload.visa or '—'}")
            user.visa = payload.visa
    if payload.region_code or payload.district_id:
        place = geo.resolve_place(db, payload.district_id, payload.region_code, None)
        if place is None:
            raise HTTPException(400, "Выберите город или район из списка")
        if place.label != user.city:
            changes.append(f"город: {user.city} → {place.label}")
        user.city, user.region_code, user.district_id = place.label, place.region_code, place.district_id
    if payload.company_name is not None and payload.company_name.strip() != user.company_name:
        changes.append(f"компания: {user.company_name or '—'} → {payload.company_name.strip() or '—'}")
        user.company_name = payload.company_name.strip()
    if payload.clear_avatar and user.avatar_key:
        photos.delete_files(user.avatar_key)
        user.avatar_key = None
        changes.append("убрано фото профиля")
    if not changes:
        return {"changed": []}
    log_action(db, actor, "user.admin_edit", "user", tg_id, "; ".join(changes)[:1500])
    db.commit()
    guard.notify_user(db, tg_id, "Администратор изменил данные вашего профиля. Если это ошибка, напишите в поддержку.", "moderation")
    return {"changed": changes}


# ---------- payments ----------
@router.get("/api/admin/payments-list")
def payments_list(q: str = Query(default="", max_length=40), status: str = Query(default="", max_length=16),
                  service: str = Query(default="", max_length=40), since: str = Query(default="", max_length=10),
                  until: str = Query(default="", max_length=10), offset: int = Query(default=0, ge=0, le=100000),
                  limit: int = Query(default=30, ge=1, le=100), actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    cond = []
    q = q.strip()
    if q:
        ids = [u.tg_id for u in db.scalars(select(User).where(or_(User.tg_id.contains(q), User.display_name.ilike(f"%{q}%"))).limit(50)).all()]
        cond.append(Payment.user_tg_id.in_(ids or ["-"]))
    if status:
        cond.append(Payment.status == status)
    if service:
        cond.append(Payment.service_code == service)
    try:
        if since:
            cond.append(Payment.created_at >= dt.datetime.fromisoformat(since).replace(tzinfo=dt.timezone.utc))
        if until:
            cond.append(Payment.created_at < dt.datetime.fromisoformat(until).replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1))
    except ValueError:
        raise HTTPException(400, "Дата в формате ГГГГ-ММ-ДД")
    total = db.scalar(select(func.count()).select_from(Payment).where(*cond)) or 0
    paid_sum = db.scalar(select(func.coalesce(func.sum(Payment.stars), 0)).where(*cond, Payment.status == "paid")) or 0
    rows = db.scalars(select(Payment).where(*cond).order_by(Payment.id.desc()).limit(limit).offset(offset)).all()
    titles = {p.code: p.title for p in db.scalars(select(Pricing)).all()}
    who = names(db, [p.user_tg_id for p in rows])
    return {"total": total, "paid_stars": int(paid_sum), "services": titles, "items": [{
        "id": p.id, "user": p.user_tg_id, "name": who.get(p.user_tg_id, ""), "listing_id": p.listing_id, "service": p.service_code,
        "service_title": titles.get(p.service_code, p.service_code), "stars": p.stars, "status": p.status, "promo": p.promo_code,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "paid_at": p.paid_at.isoformat() if p.paid_at else None} for p in rows]}


# ---------- listing preview ----------
@router.get("/api/admin/listings/{listing_id}/preview", response_class=HTMLResponse)
def listing_preview(listing_id: int, with_changes: bool = Query(default=True), actor: str = Depends(staff(*MOD)),
                    db: Session = Depends(get_db)):
    """The page the way people will see it, with every picture and (optionally) the waiting edit applied.
    Nothing is saved: the changes are applied in memory and rolled back."""
    x = db.get(Listing, listing_id)
    if x is None:
        raise HTTPException(404, "Объявление не найдено")
    note = ""
    if with_changes and x.pending_changes:
        for k, v in x.pending_changes.items():
            setattr(x, k, v)
        note = " · с правкой, которая ждёт решения"
    banner = (f'<div class="card" style="background:#fff7e0;border-color:#f5a623"><b>ПРЕДПРОСМОТР</b> · статус: {html.escape(STATUS_RU.get(x.status, x.status))}{note}'
              f' · автор {html.escape(x.author_telegram_id)}</div>')
    try:
        page = site.listing_page(db, x, banner=banner, gallery=True)
    finally:
        db.rollback()          # nothing from the preview is stored
    return page


# ---------- reports with names and the object itself ----------
def report_rows(db: Session, rows) -> list[dict]:
    people = [r.reporter_tg_id for r in rows] + [r.target_id for r in rows if r.target_type == "user"]
    who = names(db, people)
    out = []
    for r in rows:
        label, link = "", ""
        if r.target_type == "listing" and r.target_id.isdigit():
            item = db.get(Listing, int(r.target_id))
            if item is not None:
                label, link = f"«{item.title}» ({item.status})", f"listing:{item.id}"
        elif r.target_type == "user":
            label, link = who.get(r.target_id) or f"ID {r.target_id}", f"user:{r.target_id}"
        elif r.target_type == "review":
            label = f"отзыв №{r.target_id}"
        elif r.target_type == "dating":
            label = f"анкета №{r.target_id}"
        out.append({"id": r.id, "target_type": r.target_type, "target_id": r.target_id, "reason": r.reason, "comment": r.comment,
                    "status": r.status, "created_at": str(r.created_at), "target_label": label, "target_link": link,
                    "reporter": r.reporter_tg_id, "reporter_name": who.get(r.reporter_tg_id, "")})
    return out
