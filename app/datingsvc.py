"""Paid services of dating profiles (VIP, «невидимка», «кто видел анкету», «супер-симпатия»), free grants, private photos,
housekeeping of the services. The payment itself goes through app/payments.py (the same Stars flow as for listings)."""
import datetime as dt
import secrets
import types
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import guard, payments
from app.admin_auth import ADM, MOD, staff
from app.audit import log_action
from app.auth import current_user_id
from app.database import get_db
from app.dating import (MAX_PHOTO_BYTES, detect_image, is_invisible, settle, upload_root, vip_kind, vip_until_eff)
from app.models import (DatingLike, DatingPrivatePhoto, DatingProfile, DatingView, Listing, Payment, Placement, Pricing, User)
from app.payments import PaymentError, aware, now
from app.users import writer

router = APIRouter()
MAX_PRIVATE = 6
DAY = dt.timedelta(days=1)

SERVICE_TEXT = {
    "bump": "Анкета встаёт в начало ленты на 24 часа (в рамках общих правил: не больше двух платных на пять мест)",
    "vip": "Метка VIP и показ выше обычных анкет (не выше закреплённых и рекламы). Срок прибавляется к оставшемуся",
    "invisible": "Анкета не показывается в ленте и поиске, но открывается по вашей ссылке. Можно включать и выключать",
    "viewers": "Список тех, кто открывал вашу анкету, за последние 30 дней",
    "super": "Супер-симпатия: вы заметнее в списке «Мне симпатизируют». Не потратили, вернётся при отмене",
}


def profile_of(db: Session, user_id: str) -> DatingProfile | None:
    return db.scalar(select(DatingProfile).where(DatingProfile.owner_tg_id == user_id))


def _extend(current: dt.datetime | None, at: dt.datetime, days: int) -> dt.datetime:
    base = aware(current) if current is not None and aware(current) > at else at
    return base + dt.timedelta(days=days)


def activate(db: Session, payment: Payment, pricing: Pricing, at: dt.datetime, promo: bool = False) -> None:
    """Give the service to the profile. Terms are added to what is left; a free promotion does not turn into a paid lift."""
    p = db.get(DatingProfile, payment.profile_id)
    if p is None:
        raise PaymentError("Анкета не найдена")
    kind = pricing.kind
    if kind == "vip":
        settle(p, at)                                   # the frozen time is added before the new term
        paid_now = vip_kind(p, at) == "vip"
        p.vip_until = _extend(p.vip_until, at, pricing.duration_days)
        p.vip_promo = bool(promo and not paid_now)     # a free grant on top of a paid VIP stays paid
        p.vip_checked_at, p.vip_notified = at, ""
    elif kind == "invisible":
        p.invisible_until = _extend(p.invisible_until, at, pricing.duration_days)
        p.invisible_on = True
    elif kind == "viewers":
        p.viewers_until = _extend(p.viewers_until, at, pricing.duration_days)
    elif kind == "bump":
        p.bumped_until = _extend(p.bumped_until, at, pricing.duration_days)
    elif kind == "super":
        p.super_credits += 1
    else:
        raise PaymentError("Эта услуга не для анкет")


def result_of(db: Session, payment: Payment) -> types.SimpleNamespace:
    """What the bot needs to say «услуга активна до …» (same shape as a Placement)."""
    p = db.get(DatingProfile, payment.profile_id)
    pricing = db.get(Pricing, payment.service_code)
    kind = pricing.kind if pricing else ""
    until = {"vip": vip_until_eff(p) if p else None, "invisible": p.invisible_until if p else None,
             "viewers": p.viewers_until if p else None, "bump": p.bumped_until if p else None}.get(kind) or now()
    return types.SimpleNamespace(ends_at=until, listing_id=None, kind=kind)


def _latest(db: Session, payment: Payment) -> bool:
    last = db.scalar(select(func.max(Payment.id)).where(
        Payment.profile_id == payment.profile_id, Payment.service_code == payment.service_code,
        Payment.status.in_(["paid", "granted", "refunded"])))
    return last == payment.id


def seconds_left(db: Session, payment: Payment, at: dt.datetime) -> float:
    """Unused time of one paid service. Several purchases of the same kind add up in the profile; the unused rest belongs to the latest."""
    p = db.get(DatingProfile, payment.profile_id)
    pricing = db.get(Pricing, payment.service_code)
    if p is None or pricing is None or not _latest(db, payment):
        return 0.0
    until = {"vip": vip_until_eff(p, at), "invisible": p.invisible_until, "viewers": p.viewers_until, "bump": p.bumped_until}.get(pricing.kind)
    if until is None:
        return 0.0
    cap = dt.timedelta(days=pricing.duration_days).total_seconds()
    return max(0.0, min(cap, (aware(until) - at).total_seconds()))


def end_service(db: Session, payment: Payment) -> None:
    """A service was returned: it ends now (only if it is the latest purchase of that kind)."""
    p = db.get(DatingProfile, payment.profile_id)
    pricing = db.get(Pricing, payment.service_code)
    if p is None or pricing is None or not _latest(db, payment):
        return
    if pricing.kind == "vip":
        p.vip_until, p.vip_promo, p.vip_notified = None, False, ""
    elif pricing.kind == "invisible":
        p.invisible_until, p.invisible_on = None, False
    elif pricing.kind == "viewers":
        p.viewers_until = None
    elif pricing.kind == "bump":
        p.bumped_until = None


# ---------- showcase and invoice ----------
def options(db: Session, p: DatingProfile) -> list[dict]:
    payments.ensure_pricing(db)
    at = now()
    out = []
    for pr in db.scalars(select(Pricing).where(Pricing.category == "dating", Pricing.is_active.is_(True)).order_by(Pricing.stars)).all():
        reason = ""
        if p.status != "published" and pr.kind in {"vip", "invisible", "bump"}:
            reason = "Услуга доступна для опубликованной анкеты"
        elif pr.kind == "bump" and (p.paused or p.suspect):
            reason = "Анкета на паузе или на проверке: поднимать пока нечего"
        until = {"vip": vip_until_eff(p, at), "invisible": p.invisible_until, "viewers": p.viewers_until, "bump": p.bumped_until}.get(pr.kind)
        out.append({"code": pr.code, "kind": pr.kind, "title": pr.title, "stars": pr.stars, "days": pr.duration_days,
                    "what": SERVICE_TEXT.get(pr.kind, ""), "available": not reason, "reason": reason,
                    "active_until": until.isoformat() if until is not None and aware(until) > at else None})
    return out


@router.get("/api/dating/services")
def my_services(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    p = profile_of(db, user_id)
    if p is None:
        raise HTTPException(404, "Сначала заполните анкету")
    user = db.get(User, user_id)
    return {"options": options(db, p), "balance": (user.balance_stars if user else 0) or 0}


class ProfileInvoiceIn(BaseModel):
    service_code: str = Field(min_length=3, max_length=40)
    promo_code: str = Field(default="", max_length=24)
    use_balance: bool = False


@router.post("/api/dating/invoice")
def profile_invoice(payload: ProfileInvoiceIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    from app import security
    security.require_flag(db, "dating_services", user_id)
    p = profile_of(db, user_id)
    if p is None:
        raise HTTPException(404, "Сначала заполните анкету")
    pricing = db.get(Pricing, payload.service_code)
    if pricing is None or not pricing.is_active or pricing.category != "dating":
        raise HTTPException(400, "Эта услуга сейчас недоступна")
    if p.status != "published" and pricing.kind in {"vip", "invisible", "bump"}:
        raise HTTPException(400, "Услуга доступна для опубликованной анкеты")
    try:
        stars, code = pricing.stars, ""
        if payload.promo_code.strip():
            promo, stars = payments.check_promo(db, user_id, payload.promo_code, pricing.stars)
            code = promo.code
        payments.require_kh(db, user_id, stars)
        taken, cash = payments.take_from_balance(db, user_id, stars, payload.use_balance or payments.balance_only())
        pay = Payment(user_tg_id=user_id, listing_id=None, profile_id=p.id, service_code=pricing.code, stars=cash,
                      payload=secrets.token_urlsafe(24), status="pending", created_at=now(), promo_code=code,
                      list_stars=pricing.stars, balance_used=taken)
        db.add(pay)
        db.commit()
        db.refresh(pay)
        if cash == 0:
            return {"payment_id": payments.pay_from_balance(db, pay).id, "invoice_url": "", "stars": 0, "paid": True, "from_balance": taken}
        url = payments.telegram_call("createInvoiceLink", {
            "title": pricing.title[:32], "description": f"Знакомства: {pricing.title}"[:255], "payload": pay.payload,
            "provider_token": "", "currency": payments.CURRENCY, "prices": [{"label": pricing.title[:32], "amount": cash}]})
    except PaymentError as exc:
        raise HTTPException(400, str(exc))
    return {"payment_id": pay.id, "invoice_url": url, "stars": cash, "paid": False, "from_balance": taken}


# ---------- free grants (gift, compensation, promotion) ----------
class GrantIn(BaseModel):
    tg_id: str = Field(min_length=1, max_length=40)
    service_code: str = Field(min_length=3, max_length=40)
    listing_id: int | None = None
    note: str = Field(default="", max_length=200)
    promo: bool = False               # a promotion: marked «по акции», no lift in the ranking


def grant(db: Session, actor: str, tg_id: str, service_code: str, listing_id: int | None = None, note: str = "",
          promo: bool = False, days: int | None = None) -> Payment:
    """Give a service without payment. It is written into the person's history with a mark; it is never refunded in Stars."""
    payments.ensure_pricing(db)
    pricing = db.get(Pricing, service_code)
    if pricing is None:
        raise PaymentError("Услуга не найдена")
    at = now()
    for_profile = pricing.category == "dating"             # 'bump' exists for listings too: the service code decides, not the kind
    if days is not None:
        pricing = types.SimpleNamespace(kind=pricing.kind, duration_days=days, code=pricing.code)
    pay = Payment(user_tg_id=tg_id, listing_id=listing_id, service_code=service_code, stars=0, payload=f"grant-{secrets.token_hex(10)}",
                  status="granted", created_at=at, paid_at=at, note=(("🎁 По акции" if promo else "🎁 Подарок") + (f": {note}" if note else "") + f" · выдал {actor}"))
    if for_profile:
        p = profile_of(db, tg_id)
        if p is None:
            raise PaymentError("У человека нет анкеты")
        pay.profile_id = p.id
        db.add(pay)
        db.flush()
        activate(db, pay, pricing, at, promo=promo)
    else:
        item = db.get(Listing, listing_id) if listing_id else None
        if item is None or item.author_telegram_id != tg_id:
            raise PaymentError("Объявление не найдено у этого человека")
        db.add(pay)
        db.flush()
        _extend_or_add_placement(db, item, pricing, at, pay, promo)
    log_action(db, actor, "grant", "user", tg_id, f"{service_code} {note}".strip())
    db.commit()
    guard.notify_user(db, tg_id, f"🎁 Вам включена услуга «{db.get(Pricing, service_code).title}»" + (f": {note}" if note else "") + ".", "payments")
    return pay


def _extend_or_add_placement(db: Session, item: Listing, pricing, at: dt.datetime, pay: Payment, promo: bool) -> None:
    for kind in payments.kinds_of(pricing):
        running = db.scalar(select(Placement).where(Placement.listing_id == item.id, Placement.kind == kind, Placement.status == "active"))
        if running is not None:            # terms are added, not replaced
            running.ends_at = aware(running.ends_at) + dt.timedelta(days=pricing.duration_days)
            running.promo = bool(running.promo and promo)
            continue
        db.add(Placement(listing_id=item.id, kind=kind, starts_at=at, ends_at=at + dt.timedelta(days=pricing.duration_days),
                         status="active", payment_id=pay.id, promo=promo))


@router.post("/api/admin/grants", status_code=201)
def admin_grant(payload: GrantIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    try:
        pay = grant(db, actor, payload.tg_id, payload.service_code, payload.listing_id, payload.note, payload.promo)
    except PaymentError as exc:
        raise HTTPException(400, str(exc))
    return {"payment_id": pay.id, "status": pay.status}


@router.get("/api/dating/me/stats")
def my_stats(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """Numbers for the cabinet: views, sympathies in both directions, mutual ones."""
    from sqlalchemy import func as f
    from app.models import DatingLike
    p = profile_of(db, user_id)
    if p is None:
        raise HTTPException(404, "Анкеты нет")
    at = now()

    def views(days: int) -> int:
        return db.scalar(select(f.count(f.distinct(DatingView.viewer_tg))).where(DatingView.profile_id == p.id, DatingView.at > at - days * DAY)) or 0
    got = {l.from_tg for l in db.scalars(select(DatingLike).where(DatingLike.to_tg == user_id)).all()}
    sent = {l.to_tg for l in db.scalars(select(DatingLike).where(DatingLike.from_tg == user_id)).all()}
    return {"views_7d": views(7), "views_30d": views(30), "likes_to_me": len(got - sent), "likes_from_me": len(sent - got),
            "mutual": len(got & sent), "super_credits": p.super_credits}


# ---------- «кто видел анкету» ----------
@router.get("/api/dating/viewers")
def viewers(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    p = profile_of(db, user_id)
    if p is None:
        raise HTTPException(404, "Анкеты нет")
    since = now() - 30 * DAY
    rows = db.scalars(select(DatingView).where(DatingView.profile_id == p.id, DatingView.at > since).order_by(DatingView.at.desc())).all()
    paid = bool(p.viewers_until and aware(p.viewers_until) > now())
    if not paid:
        return {"locked": True, "count": len({r.viewer_tg for r in rows})}
    seen, items = set(), []
    for r in rows:
        if r.viewer_tg in seen:
            continue
        seen.add(r.viewer_tg)
        other = profile_of(db, r.viewer_tg)
        if other is not None and other.status == "published" and not other.paused and not other.suspect and not is_invisible(other):
            items.append({"id": other.id, "name": other.first_name, "age": None, "at": r.at.isoformat(), "photo_url": f"/api/dating/{other.id}/photo"})
        else:
            items.append({"id": None, "name": "", "at": r.at.isoformat()})     # the person has no open profile: only the fact
    return {"locked": False, "count": len(seen), "items": items}


# ---------- private photos ----------
def may_see_private(db: Session, viewer: str, p: DatingProfile) -> bool:
    """The owner; a person with an active VIP (paid or free by a promotion); a person with whom the sympathy is mutual."""
    if viewer == p.owner_tg_id:
        return True
    mine = profile_of(db, viewer)
    if mine is not None and vip_kind(mine):
        return True
    a = db.scalar(select(DatingLike.id).where(DatingLike.from_tg == viewer, DatingLike.to_tg == p.owner_tg_id))
    b = db.scalar(select(DatingLike.id).where(DatingLike.from_tg == p.owner_tg_id, DatingLike.to_tg == viewer))
    return a is not None and b is not None


@router.post("/api/dating/private-photo", status_code=201)
async def add_private_photo(file: UploadFile = File(...), user_id: str = Depends(writer), db: Session = Depends(get_db)):
    p = profile_of(db, user_id)
    if p is None:
        raise HTTPException(400, "Сначала заполните анкету")
    if (db.scalar(select(func.count()).select_from(DatingPrivatePhoto).where(
            DatingPrivatePhoto.profile_id == p.id, DatingPrivatePhoto.status != "rejected")) or 0) >= MAX_PRIVATE:
        raise HTTPException(409, f"Не больше {MAX_PRIVATE} приватных фото")
    data = await file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Фото больше 5 МБ")
    kind = detect_image(data)
    if kind is None:
        raise HTTPException(400, "Нужна фотография в формате JPEG, PNG или WebP")
    name = f"priv-{uuid.uuid4().hex}.{kind}"
    (upload_root() / name).write_bytes(data)
    row = DatingPrivatePhoto(profile_id=p.id, key=name, status="pending")
    db.add(row)
    db.commit()
    from app import adminplus
    adminplus.notify_staff(db, "Новое приватное фото ждёт проверки (админка → Знакомства).", "dating_private")
    return {"id": row.id, "status": row.status}


@router.get("/api/dating/private-photos/mine")
def my_private_photos(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    p = profile_of(db, user_id)
    rows = db.scalars(select(DatingPrivatePhoto).where(DatingPrivatePhoto.profile_id == (p.id if p else -1))
                      .order_by(DatingPrivatePhoto.id)).all() if p else []
    return [{"id": r.id, "status": r.status, "url": f"/api/dating/{p.id}/private-photo/{r.id}"} for r in rows]


@router.delete("/api/dating/private-photo/{photo_id}")
def delete_private_photo(photo_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    p = profile_of(db, user_id)
    row = db.get(DatingPrivatePhoto, photo_id)
    if p is None or row is None or row.profile_id != p.id:
        raise HTTPException(404, "Фото не найдено")
    (upload_root() / row.key).unlink(missing_ok=True)
    db.delete(row)
    db.commit()
    return {"deleted": True}


@router.get("/api/dating/{profile_id}/private")
def private_list(profile_id: int, viewer: str = Depends(writer), db: Session = Depends(get_db)):
    """The list of private photos of a profile: addresses only for those who may see them, otherwise just the number."""
    from app.dating import _openable
    p = db.get(DatingProfile, profile_id)
    if p is None or (viewer != p.owner_tg_id and not _openable(db, p, viewer)):
        raise HTTPException(404, "Анкета не найдена")
    rows = db.scalars(select(DatingPrivatePhoto).where(DatingPrivatePhoto.profile_id == p.id, DatingPrivatePhoto.status == "live")
                      .order_by(DatingPrivatePhoto.id)).all()
    if not may_see_private(db, viewer, p):
        return {"allowed": False, "count": len(rows), "items": []}
    return {"allowed": True, "count": len(rows), "items": [{"id": r.id, "url": f"/api/dating/{p.id}/private-photo/{r.id}"} for r in rows]}


@router.get("/api/dating/{profile_id}/private-photo/{photo_id}")
def private_photo(profile_id: int, photo_id: int, viewer: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """One private photo. The right is checked on every request, nothing is cached, other people get «not found»."""
    from app.dating import _openable
    p = db.get(DatingProfile, profile_id)
    row = db.get(DatingPrivatePhoto, photo_id)
    if p is None or row is None or row.profile_id != p.id:
        raise HTTPException(404, "Фото не найдено")
    own = viewer == p.owner_tg_id
    if not own and (row.status != "live" or not _openable(db, p, viewer) or not may_see_private(db, viewer, p)):
        raise HTTPException(404, "Фото не найдено")
    return FileResponse(upload_root() / row.key, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/api/admin/dating/{profile_id}/private")
def admin_private_list(profile_id: int, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    rows = db.scalars(select(DatingPrivatePhoto).where(DatingPrivatePhoto.profile_id == profile_id).order_by(DatingPrivatePhoto.id)).all()
    return [{"id": r.id, "status": r.status} for r in rows]


@router.get("/api/admin/dating/private/{photo_id}")
def admin_private_photo(photo_id: int, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """Moderators see all private photos, every look is written to the journal."""
    row = db.get(DatingPrivatePhoto, photo_id)
    if row is None:
        raise HTTPException(404, "Фото не найдено")
    log_action(db, actor, "dating.view_private", "dating", row.profile_id, str(photo_id))
    db.commit()
    return FileResponse(upload_root() / row.key, headers={"Cache-Control": "no-store"})


@router.post("/api/admin/dating/private/{photo_id}/{decision}")
def admin_private_decision(photo_id: int, decision: str, actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    if decision not in {"approve", "reject"}:
        raise HTTPException(400, "Решение: approve или reject")
    row = db.get(DatingPrivatePhoto, photo_id)
    if row is None:
        raise HTTPException(404, "Фото не найдено")
    row.status = "live" if decision == "approve" else "rejected"
    log_action(db, actor, f"dating.private_{decision}", "dating", row.profile_id, str(photo_id))
    db.commit()
    return {"id": row.id, "status": row.status}


# ---------- housekeeping (called by the bot every few minutes) ----------
def housekeeping(db: Session) -> list[tuple[str, str, str]]:
    """Close the frozen intervals, end finished services, send reminders. Returns (user, text, kind) for the notifier."""
    at = now()
    out: list[tuple[str, str, str]] = []
    for p in db.scalars(select(DatingProfile).where(DatingProfile.vip_until.is_not(None))).all():
        until = vip_until_eff(p, at)
        settle(p, at)
        if until <= at:
            p.vip_until, p.vip_promo, p.vip_notified = None, False, ""
            out.append((p.owner_tg_id, "Срок VIP вашей анкеты закончился. Продлить можно в «Знакомства → Моя анкета».", "payments"))
        else:
            left = until - at
            if left <= DAY and p.vip_notified != "end":
                p.vip_notified = "end"
                out.append((p.owner_tg_id, "VIP вашей анкеты закончится сегодня. Продлите, чтобы остаться выше в ленте.", "payments"))
            elif left <= 3 * DAY and p.vip_notified == "":
                p.vip_notified = "3d"
                out.append((p.owner_tg_id, "VIP вашей анкеты закончится через 3 дня.", "payments"))
    for p in db.scalars(select(DatingProfile).where(DatingProfile.invisible_on.is_(True), DatingProfile.invisible_until.is_not(None))).all():
        if aware(p.invisible_until) <= at:
            p.invisible_on = False
            out.append((p.owner_tg_id, "Услуга «Невидимка» закончилась: анкета снова видна в ленте.", "payments"))
    db.commit()
    return out
