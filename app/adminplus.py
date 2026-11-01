"""The working tools of the admin panel: counters and staff alerts, search across everything, user card with notes,
editing a listing, claims («взято в работу»), undo of a decision, system status, reports and staff statistics,
sessions of staff, temporary blocks."""
import datetime as dt
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app import guard, rules
from app.admin_auth import ADM, MOD, SUP, SUPPORT, staff, _hash
from app.audit import log_action
from app.database import get_db
from app.models import (Admin, AdminSession, Application, AuditLog, Claim, ClientError, Conversation, Favorite, Heartbeat,
                        Listing, Payment, Report, Resume, Review, Ticket, TicketMessage, User, UserNote)
from app.payments import aware, now

router = APIRouter()


# ---------- staff alerts ----------
def notify_staff(db: Session, text: str, key: str, roles: tuple[str, ...] = MOD) -> int:
    """Tell the staff members who can handle it. At most one message per kind per 10 minutes for each person."""
    from app import duty
    if duty.route(db, text, key):                    # somebody is on duty for this area: only they are told (and escalation watches it)
        return 1
    sent = 0
    for adm in db.scalars(select(Admin).where(Admin.active.is_(True), Admin.role.in_(roles))).all():
        try:
            guard.rate_limit(adm.tg_id, f"staffnote:{key}", 1, 600)
        except HTTPException:
            continue
        guard.notify_user(db, adm.tg_id, text, "moderation")
        sent += 1
    return sent


# ---------- counters for the tabs ----------
def _hours(db: Session, stmt) -> int:
    oldest = db.scalar(stmt)
    return int((now() - aware(oldest)).total_seconds() // 3600) if oldest else 0


@router.get("/api/admin/counters")
def counters(actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    from app.models import DatingProfile
    from app import duty
    duty.mark_seen(db, actor)                       # opening the panel is «seen» for the duty person
    unblock_expired(db)
    c = lambda stmt: db.scalar(stmt) or 0   # noqa: E731
    pending = or_(Listing.status == "pending", (Listing.status == "approved") & (Listing.has_edit.is_(True)))
    return {
        "listings": c(select(func.count()).select_from(Listing).where(pending)),
        "listings_wait_h": _hours(db, select(func.min(Listing.created_at)).where(Listing.status == "pending")),
        "reports": c(select(func.count()).select_from(Report).where(Report.status == "new")),
        "support": c(select(func.count()).select_from(Ticket).where(Ticket.status == "open")),
        "support_wait_h": _hours(db, select(func.min(Ticket.updated_at)).where(Ticket.status == "open")),
        "resumes": c(select(func.count()).select_from(Resume).where(Resume.status == "pending")),
        "employers": c(select(func.count()).select_from(User).where(User.employer_status == "pending")),
        "dating": c(select(func.count()).select_from(DatingProfile).where(
            or_(DatingProfile.status == "pending", DatingProfile.selfie_status == "pending"))),
        "errors": c(select(func.count()).select_from(ClientError).where(ClientError.created_at >= now() - dt.timedelta(hours=24))),
        "refunds": c(text("select count(*) from refunds where status in ('new','in_progress')")),
        "companies": c(text("select count(*) from companies where status = 'pending'")),
    }


# ---------- search across everything ----------
@router.get("/api/admin/search")
def search_all(q: str = Query(min_length=1, max_length=60), actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    from app import catalog
    q = q.strip()
    out = {"listings": [], "users": [], "tickets": [], "payments": []}
    if q.lstrip("#").isdigit():
        n = int(q.lstrip("#"))
        item = db.get(Listing, n)
        if item is not None:
            out["listings"].append(item)
        t = db.get(Ticket, n)
        if t is not None:
            out["tickets"].append({"id": t.id, "user": t.user_tg_id, "kind": t.kind, "status": t.status})
        pay = db.get(Payment, n)
        if pay is not None:
            out["payments"].append({"id": pay.id, "user": pay.user_tg_id, "stars": pay.stars, "status": pay.status})
    users = db.scalars(select(User).where(or_(User.tg_id == q, User.display_name.ilike(f"%{q}%"))).limit(8)).all()
    out["users"] = [{"tg_id": u.tg_id, "name": u.display_name, "city": u.city, "blocked": u.blocked} for u in users]
    words = q.lower().split()[:4]
    stmt = select(Listing)
    for w in words:
        stmt = stmt.where(Listing.search_text.like(catalog._like(w), escape="\\"))
    found = list(db.scalars(stmt.order_by(Listing.id.desc()).limit(8)).all())
    seen = {x.id for x in out["listings"]}
    out["listings"] += [x for x in found if x.id not in seen]
    out["listings"] = [{"id": x.id, "title": x.title, "status": x.status, "category": x.category, "city": x.city,
                        "author_id": x.author_telegram_id} for x in out["listings"][:8]]
    return out


# ---------- the card of a person ----------
class NoteIn(BaseModel):
    text: str = Field(min_length=2, max_length=500)


def _dating_brief(db: Session, tg_id: str):
    from app.models import DatingProfile
    p = db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == tg_id))
    return None if p is None else {"id": p.id, "status": p.status, "selfie": p.selfie_status, "paused": p.paused}


def _company_names(db: Session, tg_id: str):
    from app.company import Company, CompanyMember
    out = []
    for m in db.scalars(select(CompanyMember).where(CompanyMember.tg_id == tg_id)).all():
        c = db.get(Company, m.company_id)
        if c is not None:
            out.append({"id": c.id, "name": c.name, "role": m.role, "status": c.status})
    return out


@router.get("/api/admin/users/{tg_id}/card")
def user_card(tg_id: str, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    user = db.get(User, tg_id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    from app import security
    security.note_pii_view(db, actor, "user", tg_id)
    listings = db.scalars(select(Listing).where(Listing.author_telegram_id == tg_id).order_by(Listing.id.desc()).limit(30)).all()
    c = lambda stmt: db.scalar(stmt) or 0   # noqa: E731
    paid = c(select(func.coalesce(func.sum(Payment.stars), 0)).where(Payment.user_tg_id == tg_id, Payment.status == "paid"))
    tickets = db.scalars(select(Ticket).where(Ticket.user_tg_id == tg_id).order_by(Ticket.id.desc()).limit(10)).all()
    notes = db.scalars(select(UserNote).where(UserNote.user_tg_id == tg_id).order_by(UserNote.id.desc()).limit(30)).all()
    return {
        "balance": user.balance_stars or 0, "email": user.email or "", "has_password": bool(user.password_hash), "tg_id": tg_id, "name": user.display_name, "city": user.city, "visa": user.visa, "blocked": user.blocked,
        "block_reason": user.block_reason, "blocked_until": user.blocked_until.isoformat() if user.blocked_until else None,
        "trusted": user.trusted, "employer_status": user.employer_status, "company": user.company_name,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
        "consent": user.consent_at is not None, "deleted": user.deleted_at is not None,
        "listings": [{"id": x.id, "title": x.title, "status": x.status, "category": x.category} for x in listings],
        "payments": [{"id": p.id, "service": p.service_code, "stars": p.stars, "status": p.status, "at": str(p.paid_at or p.created_at)[:16]}
                     for p in db.scalars(select(Payment).where(Payment.user_tg_id == tg_id).order_by(Payment.id.desc()).limit(8)).all()],
        "dating": _dating_brief(db, tg_id), "companies": _company_names(db, tg_id),
        "applications": c(select(func.count()).select_from(Application).where(Application.applicant_tg_id == tg_id)),
        "chats": c(select(func.count()).select_from(Conversation).where(
            or_(Conversation.buyer_tg_id == tg_id, Conversation.seller_tg_id == tg_id))),
        "favorites": c(select(func.count()).select_from(Favorite).where(Favorite.user_tg_id == tg_id)),
        "reports_against": c(select(func.count()).select_from(Report).where(Report.target_type == "user", Report.target_id == tg_id)),
        "reports_made": c(select(func.count()).select_from(Report).where(Report.reporter_tg_id == tg_id)),
        "reviews_got": c(select(func.count()).select_from(Review).where(Review.target_tg_id == tg_id)),
        "reviews_written": c(select(func.count()).select_from(Review).where(Review.reviewer_tg_id == tg_id)),
        "stars_paid": int(paid), "has_resume": db.get(Resume, tg_id) is not None,
        "tickets": [{"id": t.id, "kind": t.kind, "status": t.status} for t in tickets],
        "notes": [{"id": n.id, "author": n.author, "text": n.text, "at": n.created_at.isoformat()} for n in notes],
    }


@router.post("/api/admin/users/{tg_id}/notes", status_code=201)
def add_note(tg_id: str, payload: NoteIn, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    if db.get(User, tg_id) is None:
        raise HTTPException(404, "Пользователь не найден")
    db.add(UserNote(user_tg_id=tg_id, author=actor, text=payload.text.strip()))
    log_action(db, actor, "user.note", "user", tg_id, payload.text[:80])
    db.commit()
    return {"ok": True}


@router.delete("/api/admin/notes/{note_id}")
def delete_note(note_id: int, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    note = db.get(UserNote, note_id)
    if note is not None:
        log_action(db, actor, "user.note_delete", "user", note.user_tg_id, note.text[:80])
        db.delete(note)
        db.commit()
    return {"deleted": True}


# ---------- temporary block ----------
class TempBlockIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)
    days: int = Field(ge=1, le=365)


@router.post("/api/admin/users/{tg_id}/block-for")
def block_for(tg_id: str, payload: TempBlockIn, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    from app import admin_ops as ops
    try:
        ops.block_user(db, actor, tg_id, payload.reason)
    except ops.OpError as exc:
        raise HTTPException(exc.status, exc.message)
    user = db.get(User, tg_id)
    user.blocked_until = now() + dt.timedelta(days=payload.days)
    log_action(db, actor, "user.block_until", "user", tg_id, f"{payload.days} дн.")
    db.commit()
    return {"tg_id": tg_id, "blocked_until": user.blocked_until.isoformat()}


def unblock_expired(db: Session) -> int:
    """Lift the blocks whose time is over. Called by the bot loop and when the panel opens."""
    from app import admin_ops as ops
    done = 0
    for u in db.scalars(select(User).where(User.blocked.is_(True), User.blocked_until.is_not(None), User.blocked_until <= now())).all():
        appeals = db.scalars(select(Ticket).where(Ticket.user_tg_id == u.tg_id, Ticket.kind == "appeal", Ticket.listing_id.is_(None),
                                                  Ticket.status == "open")).all()
        if appeals and rules.val("blocks.extend_for_appeal"):
            continue                                        # the block waits for the answer to the appeal
        try:
            ops.unblock_user(db, "system:auto", u.tg_id, "срок блокировки закончился")
        except ops.OpError:
            continue
        u.blocked_until = None
        for t in appeals:                                  # the appeal is about a block that is over
            t.status = "closed"
            db.add(TicketMessage(ticket_id=t.id, staff=True, author="system", text="Блокировка закончилась по сроку, обращение закрыто."))
        done += 1
    if done:
        db.commit()
    return done


# ---------- editing a listing by a moderator ----------
class ListingEditIn(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=3000)
    city: str | None = Field(default=None, min_length=2, max_length=100)
    price: str | None = Field(default=None, max_length=80)
    contact: str | None = Field(default=None, max_length=160)


@router.put("/api/admin/listings/{listing_id}/edit")
def admin_edit_listing(listing_id: int, payload: ListingEditIn, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    item = db.get(Listing, listing_id)
    if item is None:
        raise HTTPException(404, "Объявление не найдено")
    changes = {}
    for key, value in payload.model_dump(exclude_none=True).items():
        value = value.strip()
        if getattr(item, key) != value:
            changes[key] = (getattr(item, key), value)
            setattr(item, key, value)
    if not changes:
        return {"changed": []}
    log_action(db, actor, "listing.admin_edit", "listing", listing_id, "; ".join(f"{k}: {str(a)[:40]} → {str(b)[:40]}" for k, (a, b) in changes.items()))
    db.commit()
    guard.notify_user(db, item.author_telegram_id, f"Модератор поправил объявление «{item.title}»: {', '.join(changes)}.", "moderation")
    return {"changed": list(changes)}


# ---------- «взято в работу» ----------
class ClaimIn(BaseModel):
    kind: str = Field(pattern="^(listing|ticket|report|resume)$")
    id: str = Field(max_length=40)
    release: bool = False


CLAIM_MINUTES = 15


@router.post("/api/admin/claim")
def claim(payload: ClaimIn, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    row = db.get(Claim, (payload.kind, payload.id))
    busy = row is not None and aware(row.until) > now() and row.staff != actor
    if payload.release:
        if row is not None and not busy:
            db.delete(row)
            db.commit()
        return {"claimed": False}
    if busy:
        raise HTTPException(409, f"Уже в работе у сотрудника {row.staff}")
    if row is None:
        row = Claim(kind=payload.kind, obj_id=payload.id, staff=actor, until=now())
    row.staff, row.until = actor, now() + dt.timedelta(minutes=rules.val("moderation.claim_minutes"))
    db.add(row)
    db.commit()
    return {"claimed": True, "until": row.until.isoformat()}


@router.get("/api/admin/claims")
def claims(kind: str = Query(max_length=12), actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    rows = db.scalars(select(Claim).where(Claim.kind == kind, Claim.until > now())).all()
    return {r.obj_id: r.staff for r in rows}


# ---------- undo the last moderation decision ----------
UNDO_MINUTES = 30      # default; the working value is rules.val('moderation.undo_minutes')
DECISION_RE = re.compile(r"^(\w+) -> (\w+)")


@router.post("/api/admin/listings/{listing_id}/undo")
def undo_decision(listing_id: int, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    item = db.get(Listing, listing_id)
    if item is None:
        raise HTTPException(404, "Объявление не найдено")
    last = db.scalar(select(AuditLog).where(AuditLog.object_type == "listing", AuditLog.object_id == str(listing_id),
                                            AuditLog.action.in_(["listing.approve", "listing.reject", "listing.needs_fix", "listing.hide", "listing.show"]))
                     .order_by(AuditLog.id.desc()).limit(1))
    m = DECISION_RE.match(last.details or "") if last else None
    if last is None or m is None or (now() - aware(last.created_at)) > dt.timedelta(minutes=rules.val("moderation.undo_minutes")):
        raise HTTPException(409, f"Отменить можно только последнее решение, не старше {rules.val('moderation.undo_minutes')} минут")
    before, after = m.group(1), m.group(2)
    if item.status != after:
        raise HTTPException(409, "Статус уже изменился после этого решения")
    item.status = before
    if before == "pending":
        item.note = ""
    log_action(db, actor, "listing.undo", "listing", listing_id, f"{after} -> {before}")
    db.commit()
    guard.notify_user(db, item.author_telegram_id, f"Решение по объявлению «{item.title}» пересматривается.", "moderation")
    return {"id": item.id, "status": item.status}


# ---------- system status ----------
@router.get("/api/admin/system")
def system_status(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    import shutil
    from app import observe
    from app.config import settings
    beat = db.get(Heartbeat, "bot")
    age = int((now() - aware(beat.at)).total_seconds()) if beat is not None else None
    try:
        db.execute(select(1))
        db_ok = True
    except Exception:
        db_ok = False
    try:
        free = shutil.disk_usage(settings.upload_dir if __import__("os").path.exists(settings.upload_dir) else ".").free // (1024 * 1024)
    except Exception:
        free = None
    errors = db.scalar(select(func.count()).select_from(ClientError).where(ClientError.created_at >= now() - dt.timedelta(hours=24))) or 0
    return {"db": db_ok, "bot_age_s": age, "bot_ok": age is not None and age < 300, "disk_free_mb": free,
            "cert_days": observe.certificate_days(), "client_errors_24h": errors,
            "users": db.scalar(select(func.count()).select_from(User)) or 0}


# ---------- reports summary and staff statistics ----------
@router.get("/api/admin/reports-summary")
def reports_summary(days: int = Query(default=30, ge=1, le=180), actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    since = now() - dt.timedelta(days=days)
    base = Report.created_at >= since
    by_reason = db.execute(select(Report.reason, func.count()).where(base).group_by(Report.reason).order_by(func.count().desc())).all()
    by_type = db.execute(select(Report.target_type, func.count()).where(base).group_by(Report.target_type)).all()
    by_status = dict(db.execute(select(Report.status, func.count()).where(base).group_by(Report.status)).all())
    top = db.execute(select(Report.target_type, Report.target_id, func.count()).where(base)
                     .group_by(Report.target_type, Report.target_id).order_by(func.count().desc()).limit(10)).all()
    decided = by_status.get("resolved", 0) + by_status.get("rejected", 0)
    return {"days": days, "by_reason": [{"reason": a, "n": n} for a, n in by_reason], "by_type": [{"type": a, "n": n} for a, n in by_type],
            "status": by_status, "justified_pct": round(100 * by_status.get("resolved", 0) / decided) if decided else None,
            "top": [{"type": a, "id": b, "n": n} for a, b, n in top]}


@router.get("/api/admin/staff-detail")
def staff_detail(days: int = Query(default=30, ge=1, le=90), actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    since = now() - dt.timedelta(days=days)
    rows = db.execute(select(AuditLog.actor, AuditLog.action, func.count()).where(AuditLog.created_at >= since)
                      .group_by(AuditLog.actor, AuditLog.action)).all()
    per: dict[str, dict] = {}
    for who, action, n in rows:
        d = per.setdefault(who, {"actor": who, "approved": 0, "rejected": 0, "fixes": 0, "hidden": 0, "replies": 0, "reports": 0})
        if action in {"listing.approve", "resume.approve", "dating.approve"}:
            d["approved"] += n
        elif action in {"listing.reject", "resume.reject", "dating.reject"}:
            d["rejected"] += n
        elif action == "listing.needs_fix":
            d["fixes"] += n
        elif action == "listing.hide":
            d["hidden"] += n
        elif action == "ticket.reply":
            d["replies"] += n
        elif action.startswith("report."):
            d["reports"] += n
    out = []
    for d in per.values():
        total = d["approved"] + d["rejected"] + d["fixes"]
        d["total"] = total + d["hidden"] + d["replies"] + d["reports"]
        d["reject_pct"] = round(100 * (d["rejected"] + d["fixes"]) / total) if total else None
        out.append(d)
    return sorted(out, key=lambda x: -x["total"])


# ---------- sessions of staff ----------
@router.get("/api/admin/my-sessions")
def my_sessions(actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    rows = db.scalars(select(AdminSession).where(AdminSession.tg_id == actor, AdminSession.expires_at > now())
                      .order_by(AdminSession.expires_at.desc())).all()
    return [{"started": r.created_at.isoformat() if r.created_at else None, "expires": r.expires_at.isoformat()} for r in rows]


@router.post("/api/admin/logout-all")
def logout_all(actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    n = 0
    for r in db.scalars(select(AdminSession).where(AdminSession.tg_id == actor)).all():
        db.delete(r)
        n += 1
    log_action(db, actor, "admin.logout_all", "admin", actor, f"сессий: {n}")
    db.commit()
    return {"closed": n}


@router.post("/api/admin/staff/{tg_id}/force-logout")
def force_logout(tg_id: str, actor: str = Depends(staff(*SUP)), db: Session = Depends(get_db)):
    n = 0
    for r in db.scalars(select(AdminSession).where(AdminSession.tg_id == tg_id)).all():
        db.delete(r)
        n += 1
    log_action(db, actor, "admin.force_logout", "admin", tg_id, f"сессий: {n}")
    db.commit()
    return {"closed": n}


@router.get("/api/admin/staff-logins")
def staff_logins(actor: str = Depends(staff(*SUP)), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.action.like("admin.%")).order_by(AuditLog.id.desc()).limit(60)).all()
    return [{"actor": r.actor, "action": r.action, "details": r.details, "at": r.created_at.isoformat()} for r in rows]


# ---------- bulk actions ----------
class BulkIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=50)


@router.post("/api/admin/resumes-bulk/{decision}")
def resumes_bulk(decision: str, payload: BulkIn, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    if decision != "approve":
        raise HTTPException(400, "Массово можно только опубликовать")
    n = 0
    for owner in payload.ids:
        r = db.get(Resume, owner)
        if r is not None and r.status == "pending":
            r.status, r.note = "approved", ""
            log_action(db, actor, "resume.approve", "resume", owner, "bulk")
            guard.notify_user(db, owner, "Ваше резюме опубликовано: работодатели могут его найти.", "moderation")
            n += 1
    db.commit()
    return {"done": n}


@router.post("/api/admin/tickets-bulk-close")
def tickets_bulk_close(payload: BulkIn, actor: str = Depends(staff(*SUPPORT)), db: Session = Depends(get_db)):
    n = 0
    for tid in payload.ids:
        t = db.get(Ticket, int(tid)) if tid.isdigit() else None
        if t is not None and t.status != "closed":
            t.status, t.updated_at = "closed", now()
            n += 1
    log_action(db, actor, "ticket.bulk_close", "ticket", 0, f"закрыто: {n}")
    db.commit()
    return {"done": n}
