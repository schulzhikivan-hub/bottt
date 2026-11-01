"""Paid services in Telegram Stars (ТЗ № 2).

Rules implemented here:
- Currency is XTR (Telegram Stars). No provider token, no other payment methods.
- A placement is activated only after the server receives successful_payment
  for a pending payment created by the server. Client data is never trusted.
- Idempotency: the Telegram charge ID is unique in the DB; repeated
  notifications return the existing result and never activate twice.
- Refunds go through refundStarPayment and end the placement.
"""
import datetime as dt
import secrets

import httpx
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Listing, Payment, Placement, Pricing, PromoCode, User

CURRENCY = "XTR"
INVOICE_TTL = dt.timedelta(hours=1)
BUMP_COOLDOWN = dt.timedelta(hours=24)

# Starting prices from ТЗ № 2. Administrators edit them in the admin panel.
PRICING_SEED = [
    ("job_bump", "job", "bump", "Поднять вакансию", 30, 1),
    ("job_vip", "job", "vip", "VIP-вакансия на 7 дней", 100, 7),
    ("job_highlight", "job", "highlight", "Выделение цветом", 20, 7),
    ("job_pin", "job", "pin", "Закрепление на 7 дней", 70, 7),
    ("housing_bump", "housing", "bump", "Поднять объявление", 20, 1),
    ("housing_vip", "housing", "vip", "VIP на 7 дней", 70, 7),
    ("housing_pin", "housing", "pin", "Закрепление", 50, 7),
    ("marketplace_bump", "marketplace", "bump", "Поднять товар", 15, 1),
    ("marketplace_vip", "marketplace", "vip", "VIP на 7 дней", 50, 7),
    ("marketplace_highlight", "marketplace", "highlight", "Выделение объявления", 15, 7),
    ("dating_vip", "dating", "vip", "VIP-анкета на 7 дней", 70, 7),
    ("dating_invisible", "dating", "invisible", "Невидимка на 30 дней", 60, 30),
    ("dating_viewers", "dating", "viewers", "Кто видел анкету, 30 дней", 40, 30),
    ("dating_super", "dating", "super", "Супер-симпатия (1 шт.)", 15, 0),
]
# Higher number = shown higher in the listing feed
KIND_PRIORITY = {"pin": 4, "vip": 3, "bump": 2, "highlight": 1}


class PaymentError(Exception):
    """Error with a message that can be shown to the user."""


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def aware(value: dt.datetime) -> dt.datetime:
    """SQLite returns naive datetimes; treat them as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def ensure_pricing(db: Session) -> None:
    """Fill in the services that are missing (new services appear on existing servers too); prices already set stay as they are."""
    have = set(db.scalars(select(Pricing.code)).all())
    added = False
    for code, category, kind, title, stars, days in PRICING_SEED:
        if code not in have:
            db.add(Pricing(code=code, category=category, kind=kind, title=title, stars=stars, duration_days=days, is_active=True))
            added = True
    if added:
        db.commit()


def telegram_call(method: str, payload: dict) -> dict:
    """Call Telegram Bot API. Replaced in tests."""
    url = f"https://api.telegram.org/bot{settings.bot_token}/{method}"
    response = httpx.post(url, json=payload, timeout=15)
    data = response.json()
    if not data.get("ok"):
        raise PaymentError(f"Telegram: {data.get('description', 'ошибка')}")
    return data["result"]


def active_placements(db: Session, listing_ids: list[int]) -> dict[int, str]:
    """Return {listing_id: highest-priority active kind} for feed ordering."""
    if not listing_ids:
        return {}
    rows = db.scalars(select(Placement).where(
        Placement.listing_id.in_(listing_ids), Placement.status == "active")).all()
    result: dict[int, str] = {}
    for row in rows:
        current = result.get(row.listing_id)
        if current is None or KIND_PRIORITY[row.kind] > KIND_PRIORITY[current]:
            result[row.listing_id] = row.kind
    return result


def active_placement_info(db: Session, listing_ids: list[int]) -> dict[int, tuple[str, bool]]:
    """{listing_id: (highest kind, was it given free by a promotion)} for placements that are running."""
    if not listing_ids:
        return {}
    result: dict[int, tuple[str, bool]] = {}
    for row in db.scalars(select(Placement).where(Placement.listing_id.in_(listing_ids), Placement.status == "active")).all():
        cur = result.get(row.listing_id)
        if cur is None or KIND_PRIORITY[row.kind] > KIND_PRIORITY[cur[0]]:
            result[row.listing_id] = (row.kind, bool(row.promo))
    return result


KIND_TEXT = {
    "bump": "Объявление поднимается на первое место в ленте на 24 часа",
    "vip": "Метка VIP и показ выше обычных объявлений",
    "pin": "Закрепление: объявление всегда вверху списка",
    "highlight": "Цветная рамка: объявление заметнее в ленте",
}


def normalize_code(code: str) -> str:
    return "".join(code.split()).upper()[:24]


def check_promo(db: Session, user_id: str, code: str, price: int) -> tuple[PromoCode, int]:
    """Validate a promo code for this person and return (code, price after discount). A star price never drops below 1."""
    promo = db.scalar(select(PromoCode).where(PromoCode.code == normalize_code(code)))
    if promo is None or not promo.is_active:
        raise PaymentError("Промокод не найден или отключён")
    if promo.valid_until is not None and aware(promo.valid_until) < now():
        raise PaymentError("Срок действия промокода закончился")
    if promo.max_uses is not None and promo.used_count >= promo.max_uses:
        raise PaymentError("Промокод уже использован максимальное число раз")
    if db.scalar(select(Payment.id).where(Payment.user_tg_id == user_id, Payment.promo_code == promo.code,
                                          Payment.status == "paid")) is not None:
        raise PaymentError("Вы уже использовали этот промокод")
    off = price * promo.percent // 100 if promo.percent else (promo.fixed_stars or 0)
    return promo, max(1, price - off)


def service_options(db: Session, listing: Listing) -> list[dict]:
    """The services for this listing with what can be bought right now and why not (shown on the showcase)."""
    ensure_pricing(db)
    current = now()
    out = []
    for p in db.scalars(select(Pricing).where(Pricing.category == listing.category, Pricing.is_active == True)  # noqa: E712
                        .order_by(Pricing.stars)).all():
        reason, until = "", None
        if listing.status != "approved":
            reason = "Услуги доступны для опубликованных объявлений"
        elif p.kind == "bump":
            last = db.scalar(select(Placement).where(Placement.listing_id == listing.id, Placement.kind == "bump")
                             .order_by(Placement.starts_at.desc()).limit(1))
            if last and aware(last.starts_at) > current - BUMP_COOLDOWN:
                left = aware(last.starts_at) + BUMP_COOLDOWN - current
                reason = f"Поднять можно через {max(1, int(left.total_seconds() // 3600) + 1)} ч"
        else:
            busy = db.scalar(select(Placement).where(Placement.listing_id == listing.id, Placement.kind == p.kind,
                                                     Placement.status == "active", Placement.ends_at > current))
            if busy is not None:
                until, reason = busy.ends_at, "Уже действует"
        out.append({"code": p.code, "kind": p.kind, "title": p.title, "stars": p.stars, "days": p.duration_days,
                    "what": KIND_TEXT.get(p.kind, ""), "available": not reason, "reason": reason, "active_until": until})
    return out


def take_from_balance(db: Session, user_id: str, stars: int, use_balance: bool) -> tuple[int, int]:
    """(taken from the balance, left to pay in Stars). The balance is reserved at once and returned if the invoice is not paid."""
    if not use_balance or stars <= 0:
        return 0, stars
    user = db.get(User, user_id)
    have = (user.balance_stars if user is not None else 0) or 0
    taken = min(have, stars)
    if taken > 0:
        user.balance_stars = have - taken
    return taken, stars - taken


def restore_stale_balance(db: Session) -> int:
    """Invoices that were never paid give the reserved balance back."""
    n = 0
    for p in db.scalars(select(Payment).where(Payment.status == "pending", Payment.balance_used > 0)).all():
        if aware(p.created_at) < now() - INVOICE_TTL:
            user = db.get(User, p.user_tg_id)
            if user is not None:
                user.balance_stars = (user.balance_stars or 0) + p.balance_used
            p.status, p.note = "failed", (p.note + " баланс возвращён").strip()
            n += 1
    if n:
        db.commit()
    return n


def create_invoice(db: Session, user_id: str, listing_id: int, service_code: str,
                   promo_code: str = "", use_balance: bool = False) -> tuple[Payment, str]:
    """Create a pending payment and a Telegram invoice link. Returns (payment, url)."""
    pricing = db.get(Pricing, service_code)
    if pricing is None or not pricing.is_active:
        raise PaymentError("Эта услуга сейчас недоступна")
    listing = db.get(Listing, listing_id)
    if listing is None or listing.author_telegram_id != user_id:
        raise PaymentError("Объявление не найдено")
    if listing.status != "approved":
        raise PaymentError("Услуги доступны только для опубликованных объявлений")
    if listing.category != pricing.category:
        raise PaymentError("Эта услуга не подходит для данного раздела")

    current = now()
    if pricing.kind == "bump":
        last_bump = db.scalar(select(Placement).where(
            Placement.listing_id == listing_id, Placement.kind == "bump")
            .order_by(Placement.starts_at.desc()).limit(1))
        if last_bump and aware(last_bump.starts_at) > current - BUMP_COOLDOWN:
            raise PaymentError("Поднимать объявление можно раз в 24 часа")
    else:
        busy = db.scalar(select(Placement).where(
            Placement.listing_id == listing_id, Placement.kind == pricing.kind,
            Placement.status == "active"))
        if busy is not None:
            raise PaymentError("Эта услуга уже активна. Дождитесь окончания")

    stars, used_code = pricing.stars, ""
    if promo_code.strip():
        promo, stars = check_promo(db, user_id, promo_code, pricing.stars)
        used_code = promo.code
    taken, cash = take_from_balance(db, user_id, stars, use_balance)
    payment = Payment(user_tg_id=user_id, listing_id=listing_id, service_code=service_code,
                      stars=cash, payload=secrets.token_urlsafe(24), status="pending",
                      created_at=current, promo_code=used_code, list_stars=pricing.stars, balance_used=taken)
    db.add(payment)
    db.commit()
    db.refresh(payment)
    if cash == 0:        # the balance covers everything: no invoice, the service starts at once
        return pay_from_balance(db, payment), ""

    result = telegram_call("createInvoiceLink", {
        "title": pricing.title[:32],
        "description": f"#{listing.id} {listing.title}"[:255],
        "payload": payment.payload,
        "provider_token": "",
        "currency": CURRENCY,
        "prices": [{"label": pricing.title[:32], "amount": stars}],
    })
    return payment, result


def pay_from_balance(db: Session, payment: Payment) -> Payment:
    """Finish a payment that was paid entirely from the balance (no Telegram invoice)."""
    pricing = db.get(Pricing, payment.service_code)
    current = now()
    payment.status, payment.paid_at = "paid", current
    payment.telegram_payment_charge_id = f"balance-{payment.id}"
    if payment.profile_id:
        from app import datingsvc
        datingsvc.activate(db, payment, pricing, current)
    else:
        db.add(Placement(listing_id=payment.listing_id, kind=pricing.kind, starts_at=current,
                         ends_at=current + dt.timedelta(days=pricing.duration_days), status="active", payment_id=payment.id))
    db.commit()
    return payment


def _payment_by_payload(db: Session, payload: str) -> Payment | None:
    return db.scalar(select(Payment).where(Payment.payload == payload))


def check_pre_checkout(db: Session, payload: str, user_id: str, currency: str, amount: int) -> str | None:
    """Return None if the payment may proceed, otherwise an error for the user."""
    payment = _payment_by_payload(db, payload)
    if payment is None:
        return "Счёт не найден"
    if payment.user_tg_id != user_id:
        return "Счёт выставлен на другого пользователя"
    if payment.status != "pending":
        return "Этот счёт уже обработан"
    if aware(payment.created_at) < now() - INVOICE_TTL:
        return "Срок счёта истёк. Создайте новый"
    if currency != CURRENCY or amount != payment.stars:
        return "Сумма или валюта не совпадают"
    return None


def process_successful_payment(db: Session, user_id: str, payload: str,
                               charge_id: str, currency: str, amount: int) -> Placement:
    """Activate the purchased service. Safe to call repeatedly for the same charge."""
    existing = db.scalar(select(Placement).join(Payment, Payment.id == Placement.payment_id)
                         .where(Payment.telegram_payment_charge_id == charge_id))
    if existing is not None:
        return existing  # already processed: do not activate twice
    done = db.scalar(select(Payment).where(Payment.telegram_payment_charge_id == charge_id))
    if done is not None and done.profile_id:
        from app import datingsvc
        return datingsvc.result_of(db, done)

    payment = _payment_by_payload(db, payload)
    if payment is None or payment.user_tg_id != user_id:
        raise PaymentError("Платёж не распознан")
    if currency != CURRENCY or amount != payment.stars:
        raise PaymentError("Сумма платежа не совпадает со счётом")
    if payment.status == "paid":
        raise PaymentError("Платёж уже обработан")
    if payment.status != "pending":
        raise PaymentError("Счёт неактивен")

    pricing = db.get(Pricing, payment.service_code)
    if pricing is None:
        raise PaymentError("Услуга не найдена")

    current = now()
    payment.status = "paid"
    payment.telegram_payment_charge_id = charge_id
    payment.paid_at = current
    if payment.promo_code:
        used = db.scalar(select(PromoCode).where(PromoCode.code == payment.promo_code))
        if used is not None:
            used.used_count += 1
    if payment.profile_id:           # a dating service: the profile gets it, there is no placement of a listing
        from app import datingsvc
        datingsvc.activate(db, payment, pricing, current)
        db.commit()
        return datingsvc.result_of(db, payment)
    placement = Placement(listing_id=payment.listing_id, kind=pricing.kind, starts_at=current,
                          ends_at=current + dt.timedelta(days=pricing.duration_days),
                          status="active", payment_id=payment.id)
    db.add(placement)
    try:
        db.commit()
    except IntegrityError:
        # Parallel duplicate: another request already stored this charge
        db.rollback()
        existing = db.scalar(select(Placement).join(Payment, Payment.id == Placement.payment_id)
                             .where(Payment.telegram_payment_charge_id == charge_id))
        if existing is None:
            raise
        return existing
    db.refresh(placement)
    return placement


def expire_placements(db: Session, at: dt.datetime | None = None) -> list[Placement]:
    """End placements whose time is over. Returns those that just ended (for notifications)."""
    moment = at or now()
    ended = []
    for placement in db.scalars(select(Placement).where(Placement.status == "active")).all():
        if placement.frozen_at is None and aware(placement.ends_at) <= moment:    # a frozen clock does not run
            placement.status = "ended"
            ended.append(placement)
    db.commit()
    return ended


def placements_ending_soon(db: Session, hours: int = 24, at: dt.datetime | None = None) -> list[Placement]:
    """Active services that end within `hours` and whose owner has not been reminded yet (marks them as reminded)."""
    moment = at or now()
    soon = [p for p in db.scalars(select(Placement).where(Placement.status == "active", Placement.reminded.is_(False))).all()
            if moment < aware(p.ends_at) <= moment + dt.timedelta(hours=hours) and p.kind != "bump"]
    for p in soon:
        p.reminded = True
    db.commit()
    return soon


def refund(db: Session, payment_id: int) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise PaymentError("Платёж не найден")
    if payment.status != "paid" or not payment.telegram_payment_charge_id:
        raise PaymentError("Вернуть можно только оплаченный платёж")
    if (payment.stars or 0) > 0:      # the cash part goes back through Telegram, the balance part back to the balance
        telegram_call("refundStarPayment", {
            "user_id": int(payment.user_tg_id),
            "telegram_payment_charge_id": payment.telegram_payment_charge_id,
        })
    if payment.balance_used:
        user = db.get(User, payment.user_tg_id)
        if user is not None:
            user.balance_stars = (user.balance_stars or 0) + payment.balance_used
    payment.status = "refunded"
    for placement in db.scalars(select(Placement).where(Placement.payment_id == payment.id)).all():
        placement.status = "refunded"
    if payment.profile_id:
        from app import datingsvc
        datingsvc.end_service(db, payment)
    db.commit()
    return payment


def remaining_fraction(db: Session, payment: Payment, at: dt.datetime | None = None) -> float:
    """How much of a paid service is still unused (0..1), looking at the real clock (frozen time is not counted as used)."""
    at = at or now()
    pricing = db.get(Pricing, payment.service_code)
    if pricing is None or pricing.duration_days <= 0:
        return 0.0
    total = dt.timedelta(days=pricing.duration_days).total_seconds()
    if payment.profile_id:
        from app import datingsvc
        left = datingsvc.seconds_left(db, payment, at)
    else:
        pl = db.scalar(select(Placement).where(Placement.payment_id == payment.id, Placement.status == "active"))
        if pl is None:
            return 0.0
        edge = aware(pl.frozen_at) if pl.frozen_at else at
        left = max(0.0, (aware(pl.ends_at) - edge).total_seconds())
    return max(0.0, min(1.0, left / total))


def refund_to_balance(db: Session, payment_id: int, reason: str = "") -> tuple[Payment, int]:
    """Proportional refund: the unused part of a paid service becomes Stars on the person's balance (Telegram can only
    return a whole payment). Free grants return nothing. The service ends. Returns (payment, stars credited)."""
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise PaymentError("Платёж не найден")
    if payment.status != "paid":
        raise PaymentError("Вернуть можно только оплаченную услугу")
    paid_real = (payment.stars or 0) + (payment.balance_used or 0)      # what the person really spent (cash + balance)
    fraction = remaining_fraction(db, payment)
    credit = int(paid_real * fraction)
    user = db.get(User, payment.user_tg_id)
    if user is not None and credit > 0:
        user.balance_stars = (user.balance_stars or 0) + credit
    payment.status = "refunded"
    payment.note = (payment.note + f" возврат на баланс {credit} ⭐ ({int(fraction * 100)}% остатка) {reason}").strip()
    for placement in db.scalars(select(Placement).where(Placement.payment_id == payment.id)).all():
        placement.status = "refunded"
    if payment.profile_id:
        from app import datingsvc
        datingsvc.end_service(db, payment)
    db.commit()
    return payment, credit


def refund_active_services(db: Session, user_id: str, reason: str = "") -> int:
    """The person is blocked by staff: unused parts of all running paid services go back to the balance. Returns Stars."""
    total = 0
    for pay in db.scalars(select(Payment).where(Payment.user_tg_id == user_id, Payment.status == "paid")).all():
        if remaining_fraction(db, pay) <= 0:
            continue
        try:
            total += refund_to_balance(db, pay.id, reason)[1]
        except PaymentError:
            continue
    return total


def mark_failed(db: Session, payload: str) -> None:
    """Payment was charged but could not be activated and was returned."""
    payment = _payment_by_payload(db, payload)
    if payment is not None and payment.status == "pending":
        payment.status = "failed"
        db.commit()


def set_price(db: Session, code: str, stars: int | None, is_active: bool | None) -> Pricing:
    pricing = db.get(Pricing, code)
    if pricing is None:
        raise PaymentError("Услуга не найдена")
    if stars is not None:
        if stars < 1 or stars > 10000:
            raise PaymentError("Цена должна быть от 1 до 10000 ⭐")
        pricing.stars = stars
    if is_active is not None:
        pricing.is_active = is_active
    db.commit()
    return pricing


# ---------- the paid time of a listing stops while the listing is not shown ----------
def sync_freeze(db: Session, listing_id: int, shown: bool, at: dt.datetime | None = None) -> None:
    """Hidden, paused, rejected or expired listing: its running services stop (frozen_at). Shown again: the stopped time is
    added back to the end, so the person does not lose paid days."""
    at = at or now()
    for pl in db.scalars(select(Placement).where(Placement.listing_id == listing_id, Placement.status == "active")).all():
        if not shown and pl.frozen_at is None:
            pl.frozen_at = at
        elif shown and pl.frozen_at is not None:
            pl.ends_at = aware(pl.ends_at) + (at - aware(pl.frozen_at))
            pl.frozen_at = None
            pl.reminded = False


def _listing_status_changed(target, value, oldvalue, initiator):
    from sqlalchemy.orm import object_session
    session = object_session(target)
    if session is None or target.id is None or value == oldvalue:
        return
    with session.no_autoflush:
        sync_freeze(session, target.id, value == "approved")


from sqlalchemy import event as _event  # noqa: E402
_event.listen(Listing.status, "set", _listing_status_changed, active_history=True)
