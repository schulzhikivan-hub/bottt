"""Anti-spam limits, automatic listing checks (ТЗ § 11.1) and Telegram notifications (ТЗ § 7.10)."""
import datetime as dt
import re
import time
from collections import defaultdict, deque

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Listing, User

# ---- rate limits (in memory; one API process) ----
_hits: dict[tuple[str, str], deque] = defaultdict(deque)


def _memory_limit(user_id: str, action: str, limit: int, window_seconds: int) -> None:
    now_ts = time.monotonic()
    bucket = _hits[(user_id, action)]
    while bucket and now_ts - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(429, "Слишком много действий. Попробуйте чуть позже")
    bucket.append(now_ts)


def rate_limit(user_id: str, action: str, limit: int, window_seconds: int) -> None:
    """Shared by every web worker (rows in `rate_hits`); falls back to memory if the DB is unreachable."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete, func
    from app.database import SessionLocal
    from app.models import RateHit
    key = f"{action}:{user_id}"[:120]
    now = datetime.now(timezone.utc)
    try:
        with SessionLocal() as db:
            db.execute(delete(RateHit).where(RateHit.bucket == key, RateHit.at < now - timedelta(seconds=window_seconds)))
            used = db.scalar(select(func.count()).select_from(RateHit).where(RateHit.bucket == key)) or 0
            if used >= limit:
                db.commit()
                raise HTTPException(429, "Слишком много действий. Попробуйте чуть позже")
            db.add(RateHit(bucket=key, at=now))
            db.commit()
    except HTTPException:
        raise
    except Exception:
        _memory_limit(user_id, action, limit, window_seconds)


def purge_rate_hits(db: Session, older_than_seconds: int = 86400) -> int:
    """Housekeeping: counters older than a day can never matter again."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete
    from app.models import RateHit
    res = db.execute(delete(RateHit).where(RateHit.at < datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)))
    db.commit()
    return res.rowcount or 0


# ---- automatic checks ----
STOP_PHRASES = [
    "предоплат", "перевод на карт", "переведите на карт", "на карту", "usdt", "криптовалют",
    "заплатите за визу", "оплатите визу", "быстрые деньги", "залог за работу", "депозит за работу",
]
PHONE_RE = re.compile(r"(\+?\d[\d\-\s()]{8,}\d)")
LINK_RE = re.compile(r"(https?://|www\.|t\.me/|@[A-Za-z][A-Za-z0-9_]{3,})", re.IGNORECASE)


def check_text(title: str, description: str) -> None:
    """Block obvious fraud patterns and contacts written into the description."""
    text = f"{title} {description}".lower()
    for phrase in STOP_PHRASES:
        if phrase in text:
            raise HTTPException(400, "Объявление содержит запрещённые условия (предоплата, перевод на карту или криптовалюту). Измените текст")
    if PHONE_RE.search(description) or LINK_RE.search(description):
        raise HTTPException(400, "Уберите телефон, ссылки и @-ники из описания: укажите контакт в поле «Контакт»")


def contact_key(contact: str) -> str:
    """Same phone written differently (+82 10-1234-5678, 01012345678) gives one key; @nick is case-insensitive."""
    c = (contact or "").strip().lower().lstrip("@")
    digits = re.sub(r"\D", "", c)
    if len(digits) >= 9:
        return digits[-9:]
    return c


def duplicate_note(db: Session, author: str, title: str, description: str, listing_id: int | None, contact: str = "") -> str:
    """Mark a listing for the moderator when the same text was posted by someone else recently."""
    rows = db.scalars(select(Listing).where(
        Listing.title == title, Listing.description == description,
        Listing.author_telegram_id != author, Listing.status.in_(["pending", "approved"]))).all()
    rows = [r for r in rows if r.id != listing_id]
    if rows:
        return f"Возможный дубликат: #{rows[0].id}"
    if len(description) >= 30:   # one text spread over several accounts is the mark of a spammer
        authors = set(db.scalars(select(Listing.author_telegram_id).where(
            Listing.description == description, Listing.author_telegram_id != author,
            Listing.status.in_(["pending", "approved", "hidden", "rejected"]))).all())
        if len(authors) >= 2:
            return f"Тот же текст у {len(authors) + 1} разных аккаунтов"
    key = contact_key(contact)
    if len(key) >= 4:     # one contact under many accounts is how spam farms look
        others = db.execute(select(Listing.author_telegram_id, Listing.contact).where(
            Listing.author_telegram_id != author, Listing.status.in_(["pending", "approved", "hidden", "rejected"]),
            Listing.contact != "")).all()
        authors = {a for a, c in others if contact_key(c) == key}
        if len(authors) >= 2:
            return f"Тот же контакт у {len(authors) + 1} разных аккаунтов"
    return ""


# ---- notifications ----
def send_telegram(tg_id: str, text: str) -> bool:
    """Send a bot message. Returns False if it could not be delivered (never raises)."""
    if not settings.bot_token or not tg_id.isdigit():
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{settings.bot_token}/sendMessage",
                       json={"chat_id": int(tg_id), "text": text[:500]}, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


NOTIFY_KINDS = {"messages": "Сообщения", "applications": "Отклики", "moderation": "Решения модератора", "support": "Ответы поддержки", "favorites": "Цены в избранном", "dating": "Знакомства",
                "searches": "Новые по моим поискам", "expiry": "Срок объявлений", "payments": "Платежи и продвижение", "promo": "Акции и новости"}


def telegram_allowed(db: Session, tg_id: str, kind: str) -> bool:
    """May this kind of notice be sent to the user's Telegram? (master switch + the kind's own switch)"""
    user = db.get(User, tg_id)
    if user is None:
        return True
    if not user.notify_messages:
        return False
    return (user.notify_prefs or {}).get(kind, True) is not False


CRITICAL_KINDS = {"payments", "moderation", "support"}      # sent at once, also at night, whatever the digest mode
DIGEST_MODES = {"now": "Сразу", "hourly": "Раз в час", "daily": "Раз в день"}
KST = dt.timezone(dt.timedelta(hours=9))


def _quiet_span() -> tuple[int, int] | None:
    raw = (settings.quiet_hours or "").strip()
    try:
        a, b = raw.split("-")
        return int(a), int(b)
    except ValueError:
        return None


def is_quiet(at: dt.datetime | None = None) -> bool:
    """Are we inside the quiet hours (Korean time, 22:00-09:00 by default)?"""
    span = _quiet_span()
    if span is None:
        return False
    hour = (at or dt.datetime.now(dt.timezone.utc)).astimezone(KST).hour
    start, end = span
    return (start <= hour or hour < end) if start > end else (start <= hour < end)


def digest_mode(user) -> str:
    mode = (user.notify_prefs or {}).get("_digest", "now") if user is not None else "now"
    return mode if mode in DIGEST_MODES else "now"


def hold_for_telegram(db: Session, tg_id: str, kind: str) -> bool:
    """Should a notice that may go to Telegram wait? Critical kinds never wait; the rest wait at night
    and when the person chose an hourly or daily digest."""
    if kind in CRITICAL_KINDS:
        return False
    return is_quiet() or digest_mode(db.get(User, tg_id)) != "now"


def mark_pending(db: Session, note_id: int) -> None:
    from app.models import Notification
    row = db.get(Notification, note_id)
    if row is not None:
        row.tg_pending = True
        db.commit()


def record_notification(db: Session, tg_id: str, text: str, kind: str = "system") -> int:
    """Put the notice in the in-app feed (always, even when Telegram is switched off). Returns its number."""
    from app.models import Notification
    row = Notification(user_tg_id=tg_id, kind=kind if kind in NOTIFY_KINDS else "system", text=text[:600])
    db.add(row)
    db.commit()
    return row.id


def notify_user(db: Session, tg_id: str, text: str, kind: str = "system") -> int:
    """Notify a user: the in-app feed always, Telegram when the global switch and the user's settings allow.
    At night and in digest modes the Telegram part waits (critical kinds do not). Returns the number of the notice."""
    note_id = record_notification(db, tg_id, text, kind)
    if not settings.notifications_enabled:
        return note_id
    if telegram_allowed(db, tg_id, kind):
        if hold_for_telegram(db, tg_id, kind):
            mark_pending(db, note_id)
        else:
            send_telegram(tg_id, text)
    return note_id


def flush_pending(db: Session, at: dt.datetime | None = None) -> list[tuple[str, str]]:
    """Held notices that may go now, one message per person (a digest when there are several). Returns (tg_id, text).
    Not during quiet hours; «hourly» waits an hour from the last digest, «daily» goes once a day after 09:00 Korean time."""
    from app.models import Notification
    at = at or dt.datetime.now(dt.timezone.utc)
    if is_quiet(at):
        return []
    out: list[tuple[str, str]] = []
    rows = db.scalars(select(Notification).where(Notification.tg_pending.is_(True)).order_by(Notification.id)).all()
    by_user: dict[str, list] = {}
    for row in rows:
        by_user.setdefault(row.user_tg_id, []).append(row)
    for tg_id, items in by_user.items():
        user = db.get(User, tg_id)
        mode = digest_mode(user)
        last = (user.notify_prefs or {}).get("_last") if user is not None else None
        last_at = dt.datetime.fromisoformat(last) if last else None
        wait = {"now": dt.timedelta(0), "hourly": dt.timedelta(hours=1), "daily": dt.timedelta(hours=23)}[mode]
        if last_at is not None and at - last_at < wait:
            continue
        for row in items:
            row.tg_pending = False
        live = [r for r in items if user is None or (user.notify_messages and (user.notify_prefs or {}).get(r.kind, True) is not False)]
        if live:
            body = live[0].text if len(live) == 1 else f"📬 Новое за это время ({len(live)}):\n" + "\n".join(f"• {r.text}" for r in live)[:3500]
            out.append((tg_id, body))
            if user is not None:
                user.notify_prefs = {**(user.notify_prefs or {}), "_last": at.isoformat()}
    db.commit()
    return out


# ---- views counter ----
_seen: dict[tuple, float] = {}


def first_view(request, listing_id: int, window_seconds: int = 3600) -> bool:
    """True if this visitor has not been counted for this listing within the last hour."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    visitor = (forwarded or (request.client.host if request.client else ""), request.headers.get("user-agent", "")[:80], listing_id)
    moment = time.monotonic()
    if len(_seen) > 50_000:
        _seen.clear()
    last = _seen.get(visitor)
    _seen[visitor] = moment
    return last is None or moment - last > window_seconds
