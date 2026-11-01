"""Honest listing views: one person counts once per listing per day; the author, the company team, staff and robots do not count.
A day-by-day table gives the author a real picture (and the numbers behind «conversion» become meaningful)."""
import datetime as dt
import hashlib

from sqlalchemy import Integer, String, UniqueConstraint, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.database import Base
from app.models import Admin, Listing
from app.payments import now

BOT_WORDS = ("bot", "crawl", "spider", "slurp", "preview", "facebookexternalhit", "whatsapp", "curl", "python-requests", "headless")


class ListingView(Base):
    __tablename__ = "listing_views"
    __table_args__ = (UniqueConstraint("listing_id", "day", "visitor", name="uq_listing_view"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(Integer, index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)        # YYYY-MM-DD
    visitor: Mapped[str] = mapped_column(String(32))                # a hash: a signed-in person or an address + browser


def _visitor(request, user_id: str | None) -> str:
    if user_id:
        raw = "u:" + user_id
    else:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        raw = "a:" + (forwarded or (request.client.host if request.client else "")) + "|" + request.headers.get("user-agent", "")[:80]
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def counts(db: Session, request, item: Listing, user_id: str | None) -> bool:
    """Does this visit count? (The author, the team, staff and robots never do.)"""
    agent = request.headers.get("user-agent", "").lower()
    if any(w in agent for w in BOT_WORDS):
        return False
    if user_id:
        if item.author_telegram_id == user_id:
            return False
        if item.company_id:
            from app import company
            if company.role_of(db, item.company_id, user_id) is not None:
                return False
        admin = db.get(Admin, user_id)
        if admin is not None and admin.active:
            return False
    return True


def record(db: Session, request, item: Listing, user_id: str | None) -> bool:
    """Count the visit once per person per day. Returns True when a new view was added."""
    if not counts(db, request, item, user_id):
        return False
    day = now().date().isoformat()
    visitor = _visitor(request, user_id)
    if db.scalar(select(ListingView.id).where(ListingView.listing_id == item.id, ListingView.day == day, ListingView.visitor == visitor)):
        return False
    db.add(ListingView(listing_id=item.id, day=day, visitor=visitor))
    item.views = (item.views or 0) + 1
    try:
        db.commit()
    except IntegrityError:                 # two parallel requests of the same person
        db.rollback()
        return False
    return True


def daily(db: Session, listing_id: int, days: int = 14) -> list[dict]:
    """Views for each of the last `days` days (zeros included), oldest first."""
    today = now().date()
    start = (today - dt.timedelta(days=days - 1)).isoformat()
    got = dict(db.execute(select(ListingView.day, func.count()).where(ListingView.listing_id == listing_id, ListingView.day >= start)
                          .group_by(ListingView.day)).all())
    return [{"day": (today - dt.timedelta(days=days - 1 - i)).isoformat(),
             "views": got.get((today - dt.timedelta(days=days - 1 - i)).isoformat(), 0)} for i in range(days)]


def purge(db: Session, keep_days: int = 120) -> int:
    cutoff = (now().date() - dt.timedelta(days=keep_days)).isoformat()
    return db.execute(delete(ListingView).where(ListingView.day < cutoff)).rowcount or 0
