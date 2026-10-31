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
from app.models import Listing, Payment, Placement, Pricing

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
    if db.scalar(select(func.count()).select_from(Pricing)):
        return
    for code, category, kind, title, stars, days in PRICING_SEED:
        db.add(Pricing(code=code, category=category, kind=kind, title=title,
                       stars=stars, duration_days=days, is_active=True))
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


def create_invoice(db: Session, user_id: str, listing_id: int, service_code: str) -> tuple[Payment, str]:
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

    payment = Payment(user_tg_id=user_id, listing_id=listing_id, service_code=service_code,
                      stars=pricing.stars, payload=secrets.token_urlsafe(24), status="pending",
                      created_at=current)
    db.add(payment)
    db.commit()
    db.refresh(payment)

    result = telegram_call("createInvoiceLink", {
        "title": pricing.title[:32],
        "description": f"#{listing.id} {listing.title}"[:255],
        "payload": payment.payload,
        "provider_token": "",
        "currency": CURRENCY,
        "prices": [{"label": pricing.title[:32], "amount": pricing.stars}],
    })
    return payment, result


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
        if aware(placement.ends_at) <= moment:
            placement.status = "ended"
            ended.append(placement)
    db.commit()
    return ended


def refund(db: Session, payment_id: int) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise PaymentError("Платёж не найден")
    if payment.status != "paid" or not payment.telegram_payment_charge_id:
        raise PaymentError("Вернуть можно только оплаченный платёж")
    telegram_call("refundStarPayment", {
        "user_id": int(payment.user_tg_id),
        "telegram_payment_charge_id": payment.telegram_payment_charge_id,
    })
    payment.status = "refunded"
    for placement in db.scalars(select(Placement).where(Placement.payment_id == payment.id)).all():
        placement.status = "refunded"
    db.commit()
    return payment


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
