"""Administrator tools beyond moderation: rejection templates, bulk actions, content (articles, groups), banners,
broadcasts, CSV export, analytics and a look at what a user does. Roles are checked on every endpoint."""
import csv
import datetime as dt
import io
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app import admin_ops as ops
from app import guard
from app.admin_auth import staff, SUPPORT, MOD, ADM
from app.audit import log_action
from app.database import get_db
from app.models import (Application, Article, Banner, Broadcast, BroadcastTarget, Conversation, Group, Listing, Message,
                        Payment, Pricing, Report, SavedSearch, User)
from app.payments import now, aware

router = APIRouter()

# Ready-made reasons: one tap for the common cases (the author sees the text)
REASON_TEMPLATES = [
    ("no_photo", "Нет фото: добавьте хотя бы одну фотографию"),
    ("prepay", "Нельзя просить предоплату или перевод на карту"),
    ("contacts", "Телефон и ссылки указывайте только в поле «Контакт», не в описании"),
    ("duplicate", "Такое объявление у вас уже опубликовано"),
    ("section", "Объявление не в том разделе: выберите подходящий"),
    ("forbidden", "Запрещённый товар или услуга"),
    ("details", "Мало подробностей: добавьте условия, цену и график"),
]
TEMPLATE_MAP = dict(REASON_TEMPLATES)


def listing_flags(db: Session, item: Listing) -> list[str]:
    """Things a moderator should look at twice: the same text or the same photo under other accounts."""
    from app.models import AuditLog
    out = []
    note = guard.duplicate_note(db, item.author_telegram_id, item.title, item.description or "", item.id)
    if note:
        out.append(note)
    last = db.scalars(select(AuditLog).where(AuditLog.action == "photo.duplicate_suspected",
                                             AuditLog.object_id == str(item.id)).order_by(AuditLog.id.desc()).limit(1)).first()
    if last is not None:
        out.append(last.details)
    return out


@router.get("/api/admin/templates")
def templates(actor: str = Depends(staff(*MOD))):
    return [{"key": k, "text": t} for k, t in REASON_TEMPLATES]


# ---------- bulk moderation ----------

class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=50)
    decision: str
    reason: str = Field(default="", max_length=300)
    template: str = ""


@router.post("/api/admin/listings/bulk")
def bulk(payload: BulkIn, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """The same decision for several listings. A failure on one listing does not stop the others."""
    reason = payload.reason or TEMPLATE_MAP.get(payload.template, "")
    if payload.decision != "approve" and len(reason.strip()) < 3:
        raise HTTPException(400, "Укажите причину или выберите шаблон")
    done, failed = [], []
    for lid in dict.fromkeys(payload.ids):
        try:
            item = ops.moderate_listing(db, actor, lid, payload.decision, reason)
            done.append(item.id)
        except ops.OpError as e:
            db.rollback()
            failed.append({"id": lid, "error": e.message})
    return {"done": done, "failed": failed}


# ---------- listings by section (not only the queue) ----------

@router.get("/api/admin/listing-list")
def listing_list(category: str | None = None, status: str | None = None, q: str = Query(default="", max_length=60),
                 limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10000),
                 actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """Any listings of one section with a status, for looking things up and hiding or restoring them."""
    from app import catalog
    stmt = select(Listing)
    if category:
        stmt = stmt.where(Listing.category == category)
    if status:
        stmt = stmt.where(Listing.status == status)
    for word in q.lower().split()[:4]:
        stmt = stmt.where(Listing.search_text.like(catalog._like(word), escape="\\"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(Listing.id.desc()).limit(limit).offset(offset)).all())
    shown = catalog.with_promo(db, rows, owner=True)
    return {"total": total, "items": [{**o.model_dump(mode="json"), "pending_changes": x.pending_changes,
                                       "author_id": x.author_telegram_id, "flags": listing_flags(db, x)} for o, x in zip(shown, rows)]}


# ---------- content: articles and groups ----------

class ArticleNew(BaseModel):
    section: str = Field(min_length=3, max_length=40)
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(default="", max_length=20000)
    source_url: str = Field(default="", max_length=255)
    checked_at: dt.datetime | None = None
    published: bool = False


@router.get("/api/admin/articles")
def admin_articles(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return [{"id": a.id, "section": a.section, "title": a.title, "body": a.body, "source_url": a.source_url,
             "checked_at": str(a.checked_at) if a.checked_at else None, "published": a.published}
            for a in db.scalars(select(Article).order_by(Article.section, Article.id)).all()]


@router.post("/api/admin/articles", status_code=201)
def add_article(payload: ArticleNew, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    if payload.published and (not payload.source_url.startswith("http") or payload.checked_at is None):
        raise HTTPException(400, "Для публикации нужны ссылка на источник (https://…) и дата проверки")
    a = Article(**payload.model_dump())
    db.add(a)
    log_action(db, actor, "article.create", "article", payload.title[:40], f"published={payload.published}")
    db.commit()
    return {"id": a.id, "published": a.published}


@router.delete("/api/admin/articles/{article_id}")
def delete_article(article_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    a = db.get(Article, article_id)
    if a is None:
        raise HTTPException(404, "Статья не найдена")
    log_action(db, actor, "article.delete", "article", article_id, a.title[:60])
    db.delete(a)
    db.commit()
    return {"deleted": True}


class GroupIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    flag: str = Field(default="", max_length=8)
    description: str = Field(default="", max_length=500)
    telegram_link: str = Field(default="", max_length=255)
    members_count: int = Field(default=0, ge=0, le=100_000_000)
    kind: str = Field(default="nation", pattern="^(nation|city)$")


def _check_link(link: str) -> None:
    if link and not link.startswith("https://t.me/"):
        raise HTTPException(400, "Ссылка должна начинаться с https://t.me/")


@router.get("/api/admin/groups")
def admin_groups(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return [{"id": g.id, "name": g.name, "flag": g.flag, "description": g.description, "telegram_link": g.telegram_link,
             "members_count": g.members_count, "kind": g.kind or "nation"} for g in db.scalars(select(Group).order_by(Group.id)).all()]


@router.post("/api/admin/groups", status_code=201)
def add_group(payload: GroupIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    _check_link(payload.telegram_link)
    g = Group(**payload.model_dump())
    db.add(g)
    log_action(db, actor, "group.create", "group", payload.name[:40], payload.telegram_link)
    db.commit()
    return {"id": g.id}


@router.put("/api/admin/groups/{group_id}/full")
def edit_group(group_id: int, payload: GroupIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    g = db.get(Group, group_id)
    if g is None:
        raise HTTPException(404, "Группа не найдена")
    _check_link(payload.telegram_link)
    for key, value in payload.model_dump().items():
        setattr(g, key, value)
    log_action(db, actor, "group.update", "group", group_id, payload.telegram_link)
    db.commit()
    return {"id": g.id}


@router.delete("/api/admin/groups/{group_id}")
def delete_group(group_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    g = db.get(Group, group_id)
    if g is None:
        raise HTTPException(404, "Группа не найдена")
    log_action(db, actor, "group.delete", "group", group_id, g.name[:60])
    db.delete(g)
    db.commit()
    return {"deleted": True}


# ---------- banners ----------

class BannerIn(BaseModel):
    text: str = Field(min_length=3, max_length=160)
    link: str = Field(default="", max_length=255)
    days: int | None = Field(default=None, ge=1, le=365)


def banner_view(b: Banner) -> dict:
    return {"id": b.id, "text": b.text, "link": b.link, "active": b.is_active,
            "ends_at": str(b.ends_at) if b.ends_at else None}


@router.get("/api/banners")
def public_banners(db: Session = Depends(get_db)):
    """What the home screen shows: active banners inside their dates, newest first (at most three)."""
    current = now()
    rows = [b for b in db.scalars(select(Banner).where(Banner.is_active.is_(True)).order_by(Banner.id.desc())).all()
            if (b.starts_at is None or aware(b.starts_at) <= current) and (b.ends_at is None or aware(b.ends_at) > current)]
    return [{"id": b.id, "text": b.text, "link": b.link} for b in rows[:3]]


@router.get("/api/admin/banners")
def admin_banners(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return [banner_view(b) for b in db.scalars(select(Banner).order_by(Banner.id.desc()).limit(50)).all()]


def create_banner(db: Session, actor: str, payload: BannerIn) -> dict:
    if payload.link and not (payload.link.startswith("https://") or payload.link.startswith("/?")):
        raise HTTPException(400, "Ссылка должна начинаться с https:// или /?")
    b = Banner(text=payload.text.strip(), link=payload.link.strip(), starts_at=now(),
               ends_at=now() + dt.timedelta(days=payload.days) if payload.days else None)
    db.add(b)
    log_action(db, actor, "banner.create", "banner", payload.text[:40], payload.link)
    db.commit()
    return banner_view(b)


@router.post("/api/admin/banners", status_code=201)
def add_banner(payload: BannerIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return create_banner(db, actor, payload)


@router.put("/api/admin/banners/{banner_id}/toggle")
def toggle_banner(banner_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    b = db.get(Banner, banner_id)
    if b is None:
        raise HTTPException(404, "Баннер не найден")
    b.is_active = not b.is_active
    log_action(db, actor, "banner.toggle", "banner", banner_id, f"active={b.is_active}")
    db.commit()
    return banner_view(b)


@router.delete("/api/admin/banners/{banner_id}")
def delete_banner(banner_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    b = db.get(Banner, banner_id)
    if b is None:
        raise HTTPException(404, "Баннер не найден")
    db.delete(b)
    log_action(db, actor, "banner.delete", "banner", banner_id, b.text[:40])
    db.commit()
    return {"deleted": True}


# ---------- broadcasts ----------

BROADCAST_PER_TICK = 25   # Telegram allows about 30 messages a second to different people


class BroadcastIn(BaseModel):
    text: str = Field(min_length=3, max_length=500)
    region_code: str | None = Field(default=None, max_length=20)


def broadcast_view(b: Broadcast) -> dict:
    return {"id": b.id, "text": b.text, "region_code": b.region_code, "status": b.status, "total": b.total,
            "sent": b.sent, "failed": b.failed, "created_at": str(b.created_at)}


def audience(db: Session, region_code: str | None) -> list[str]:
    """Who gets a broadcast: people who accepted the rules, are not blocked or deleted and did not switch Telegram notices off."""
    stmt = select(User).where(User.consent_at.is_not(None), User.blocked.is_(False), User.deleted_at.is_(None),
                              User.notify_messages.is_(True))
    if region_code:
        stmt = stmt.where(User.region_code == region_code)
    return [u.tg_id for u in db.scalars(stmt).all() if u.tg_id.isdigit()]


def create_broadcast(db: Session, actor: str, text: str, region_code: str | None) -> Broadcast:
    b = Broadcast(created_by=actor, text=text.strip(), region_code=region_code or None, status="draft",
                  total=len(audience(db, region_code)))
    db.add(b)
    log_action(db, actor, "broadcast.create", "broadcast", text[:40], f"region={region_code or 'all'} total={b.total}")
    db.commit()
    return b


def start_broadcast(db: Session, actor: str, broadcast_id: int) -> Broadcast:
    b = db.get(Broadcast, broadcast_id)
    if b is None:
        raise HTTPException(404, "Рассылка не найдена")
    if b.status != "draft":
        raise HTTPException(409, "Эта рассылка уже запущена или завершена")
    ids = audience(db, b.region_code)
    for tg in ids:
        db.add(BroadcastTarget(broadcast_id=b.id, tg_id=tg))
    b.total, b.status = len(ids), "sending" if ids else "done"
    if not ids:
        b.finished_at = now()
    log_action(db, actor, "broadcast.send", "broadcast", b.id, f"total={b.total}")
    db.commit()
    return b


def cancel_broadcast(db: Session, actor: str, broadcast_id: int) -> Broadcast:
    b = db.get(Broadcast, broadcast_id)
    if b is None:
        raise HTTPException(404, "Рассылка не найдена")
    if b.status not in ("draft", "sending"):
        raise HTTPException(409, "Рассылку уже нельзя отменить")
    b.status, b.finished_at = "cancelled", now()
    log_action(db, actor, "broadcast.cancel", "broadcast", b.id, f"sent={b.sent}")
    db.commit()
    return b


def broadcast_tick(db: Session, send) -> int:
    """Send the next batch of the oldest running broadcast. `send(tg_id, text) -> bool`. Returns how many were tried."""
    b = db.scalar(select(Broadcast).where(Broadcast.status == "sending").order_by(Broadcast.id).limit(1))
    if b is None:
        return 0
    batch = db.scalars(select(BroadcastTarget).where(BroadcastTarget.broadcast_id == b.id, BroadcastTarget.status == "pending")
                       .order_by(BroadcastTarget.id).limit(BROADCAST_PER_TICK)).all()
    for t in batch:
        ok = False
        try:
            ok = bool(send(t.tg_id, b.text))
        except Exception:
            ok = False
        t.status = "sent" if ok else "failed"
        b.sent += 1 if ok else 0
        b.failed += 0 if ok else 1
        if ok:
            guard.record_notification(db, t.tg_id, b.text, "system")
    if len(batch) < BROADCAST_PER_TICK or not db.scalar(select(func.count()).select_from(BroadcastTarget).where(
            BroadcastTarget.broadcast_id == b.id, BroadcastTarget.status == "pending")):
        b.status, b.finished_at = "done", now()
    db.commit()
    return len(batch)


@router.get("/api/admin/broadcasts")
def list_broadcasts(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return [broadcast_view(b) for b in db.scalars(select(Broadcast).order_by(Broadcast.id.desc()).limit(30)).all()]


@router.post("/api/admin/broadcasts", status_code=201)
def add_broadcast(payload: BroadcastIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return broadcast_view(create_broadcast(db, actor, payload.text, payload.region_code))


@router.post("/api/admin/broadcasts/{broadcast_id}/send")
def send_broadcast(broadcast_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return broadcast_view(start_broadcast(db, actor, broadcast_id))


@router.post("/api/admin/broadcasts/{broadcast_id}/cancel")
def stop_broadcast(broadcast_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return broadcast_view(cancel_broadcast(db, actor, broadcast_id))


# ---------- CSV export ----------

def _cell(value) -> str:
    """Spreadsheet programs run text that starts with = + - @ as a formula: neutralise it."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def make_csv(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return ("﻿" + buf.getvalue()).encode("utf-8")   # BOM so Excel reads Cyrillic correctly


@router.get("/api/admin/export/{kind}.csv")
def export_csv(kind: str, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    if kind == "users":
        header = ["id", "created_at", "city", "region", "employer", "blocked", "deleted"]
        rows = [[u.tg_id, u.created_at, u.city, u.region_code, u.employer_status, u.blocked, u.deleted_at is not None]
                for u in db.scalars(select(User).order_by(User.created_at)).all()]
    elif kind == "payments":
        titles = {p.code: p.title for p in db.scalars(select(Pricing)).all()}
        header = ["number", "created_at", "paid_at", "user", "listing", "service", "stars", "list_stars", "promo", "status"]
        rows = [[f"KH-{p.id:06d}", p.created_at, p.paid_at, p.user_tg_id, p.listing_id, titles.get(p.service_code, p.service_code),
                 p.stars, p.list_stars, p.promo_code, p.status] for p in db.scalars(select(Payment).order_by(Payment.id)).all()]
    elif kind == "listings":
        header = ["id", "created_at", "category", "title", "city", "price", "status", "views", "expires_at"]
        rows = [[x.id, x.created_at, x.category, x.title, x.city, x.price_amount if x.price_amount is not None else x.price,
                 x.status, x.views, x.expires_at] for x in db.scalars(select(Listing).order_by(Listing.id)).all()]
    else:
        raise HTTPException(404, "Неизвестная выгрузка")
    log_action(db, actor, "export", "csv", kind, f"rows={len(rows)}")
    db.commit()
    return Response(make_csv(header, rows), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="korea-hub-{kind}.csv"'})


# ---------- analytics ----------

def analytics(db: Session, days: int = 30) -> dict:
    """Per day for the last `days`: new users, listings, applications, paid stars; and the funnel views → chats → applications → paid."""
    today = now().date()
    since = today - dt.timedelta(days=days - 1)
    start = dt.datetime.combine(since, dt.time.min, tzinfo=dt.timezone.utc)
    table: dict[str, dict] = {(since + dt.timedelta(days=i)).isoformat(): {"users": 0, "listings": 0, "applications": 0, "payments": 0, "stars": 0}
                              for i in range(days)}

    def put(day, key, amount=1):
        k = aware(day).date().isoformat() if day else None
        if k in table:
            table[k][key] += amount

    for (d,) in db.execute(select(User.created_at).where(User.created_at >= start)).all():
        put(d, "users")
    for (d,) in db.execute(select(Listing.created_at).where(Listing.created_at >= start)).all():
        put(d, "listings")
    for (d,) in db.execute(select(Application.created_at).where(Application.created_at >= start)).all():
        put(d, "applications")
    by_service: dict[str, int] = defaultdict(int)
    titles = {p.code: p.title for p in db.scalars(select(Pricing)).all()}
    for p in db.scalars(select(Payment).where(Payment.status == "paid", Payment.paid_at >= start)).all():
        put(p.paid_at, "payments")
        put(p.paid_at, "stars", p.stars)
        by_service[titles.get(p.service_code, p.service_code)] += p.stars
    funnel = {
        "views": db.scalar(select(func.coalesce(func.sum(Listing.views), 0))) or 0,
        "chats": db.scalar(select(func.count()).select_from(Conversation).where(Conversation.listing_id.is_not(None))) or 0,
        "applications": db.scalar(select(func.count()).select_from(Application)) or 0,
        "paid": db.scalar(select(func.count()).select_from(Payment).where(Payment.status == "paid")) or 0,
    }
    return {"days": [{"date": k, **v} for k, v in table.items()], "funnel": funnel,
            "stars_by_service": sorted(({"service": s, "stars": n} for s, n in by_service.items()), key=lambda x: -x["stars"]),
            "totals": {"users": sum(v["users"] for v in table.values()), "listings": sum(v["listings"] for v in table.values()),
                       "applications": sum(v["applications"] for v in table.values()),
                       "stars": sum(v["stars"] for v in table.values())}}


@router.get("/api/admin/analytics")
def admin_analytics(days: int = Query(default=30, ge=1, le=90), actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return analytics(db, days)


# ---------- what a user does (for moderators and support) ----------

def user_activity(db: Session, tg_id: str) -> dict:
    user = db.get(User, tg_id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    apps = dict(db.execute(select(Application.status, func.count()).where(Application.applicant_tg_id == tg_id)
                           .group_by(Application.status)).all())
    listings = dict(db.execute(select(Listing.status, func.count()).where(Listing.author_telegram_id == tg_id)
                               .group_by(Listing.status)).all())
    searches = [{"name": s.name, "filters": s.params} for s in db.scalars(select(SavedSearch).where(SavedSearch.user_tg_id == tg_id)).all()]
    reports_on = db.scalar(select(func.count()).select_from(Report).where(Report.target_type == "user", Report.target_id == tg_id)) or 0
    reports_by = db.scalar(select(func.count()).select_from(Report).where(Report.reporter_tg_id == tg_id)) or 0
    return {"tg_id": tg_id, "city": user.city, "blocked": user.blocked, "block_reason": user.block_reason,
            "employer_status": user.employer_status, "applications": apps, "applications_total": sum(apps.values()),
            "listings": listings, "saved_searches": searches, "reports_against": reports_on, "reports_made": reports_by}


@router.get("/api/admin/users/{tg_id}/activity")
def admin_user_activity(tg_id: str, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    return user_activity(db, tg_id)


@router.get("/api/admin/reports/{report_id}/context")
def report_context(report_id: int, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    """What a moderator needs to judge a report: the listing, or the reported conversation (only the people involved)."""
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, "Жалоба не найдена")
    out = {"report": {"id": r.id, "type": r.target_type, "target": r.target_id, "reason": r.reason, "comment": r.comment, "status": r.status},
           "listing": None, "messages": []}
    if r.target_type == "listing" and r.target_id.isdigit():
        item = db.get(Listing, int(r.target_id))
        if item is not None:
            out["listing"] = {"id": item.id, "title": item.title, "status": item.status, "author": item.author_telegram_id}
    if r.target_type in ("user", "listing"):
        other = r.target_id if r.target_type == "user" else (out["listing"] or {}).get("author")
        if other:
            conv = db.scalar(select(Conversation).where(
                ((Conversation.buyer_tg_id == r.reporter_tg_id) & (Conversation.seller_tg_id == other))
                | ((Conversation.seller_tg_id == r.reporter_tg_id) & (Conversation.buyer_tg_id == other))).limit(1))
            if conv is not None:
                for m in reversed(db.scalars(select(Message).where(Message.conversation_id == conv.id)
                                             .order_by(Message.id.desc()).limit(30)).all()):
                    out["messages"].append({"from": "reporter" if m.sender_tg_id == r.reporter_tg_id else "reported",
                                            "text": m.text or "[фото]", "at": str(m.created_at)})
    log_action(db, actor, "report.view_context", "report", report_id, f"messages={len(out['messages'])}")
    db.commit()
    return out
