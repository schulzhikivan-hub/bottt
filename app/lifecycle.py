"""Life of a listing: publication, edits of a published listing, expiry and renewal.

draft -> pending -> approved -> (expired | closed | sold | archived)

An edit of a published listing does not take it down. Small changes (schedule, number of places, area…)
apply at once. Important ones (title, text, price, contact, place, company details) and photo changes
wait for a moderator while the old version stays visible.
"""
import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import photos
from app.config import settings
from app.models import Listing, ListingPhoto
from app.payments import now, aware

# Changing these on a published listing needs a moderator
CRITICAL = {"title", "description", "contact", "price", "price_amount", "city", "region_code", "district_id", "details"}
PHOTO_SECTIONS = {"housing", "marketplace"}  # sections that need at least one photo


def needs_photo(db: Session, item: Listing) -> bool:
    """True when the section requires a photo and the listing has none."""
    if item.category == "marketplace" and item.goods_category == "services":
        return False  # a service may be offered without a picture
    return (settings.require_photos and item.category in PHOTO_SECTIONS
            and photos.count_photos(db, item.id) == 0)


def _photo_rows(db: Session, item: Listing) -> list[ListingPhoto]:
    return list(db.scalars(select(ListingPhoto).where(ListingPhoto.listing_id == item.id)).all())


def show_waiting_photos(db: Session, item: Listing) -> None:
    for p in _photo_rows(db, item):
        if p.status == "pending":
            p.status = "live"
        elif p.status == "removal":
            photos.delete_files(p.key)
            db.delete(p)


def discard_waiting_photos(db: Session, item: Listing) -> None:
    for p in _photo_rows(db, item):
        if p.status == "pending":
            photos.delete_files(p.key)
            db.delete(p)
        elif p.status == "removal":
            p.status = "live"


def publish(db: Session, item: Listing) -> None:
    """First approval: the listing goes live for the configured number of days."""
    moment = now()
    item.status = "approved"
    item.note = ""
    item.pending_changes = None
    item.published_at = item.published_at or moment
    item.expires_at = moment + dt.timedelta(days=settings.listing_ttl_days)
    item.expiry_notified = False
    item.expiry_notified_1d = False
    item.expiry_notified_7d = False
    show_waiting_photos(db, item)
    db.flush()
    photos.refresh_edit_flag(db, item)


def apply_edit(db: Session, item: Listing) -> None:
    """A moderator approved the waiting edit: the new version replaces the old one."""
    old_price = item.price_amount
    for key, value in (item.pending_changes or {}).items():
        setattr(item, key, value)
    item.pending_changes = None
    item.note = ""
    if old_price and item.price_amount is not None and item.price_amount < old_price:
        from app import extras
        extras.price_dropped(db, item, old_price)
    show_waiting_photos(db, item)
    db.flush()
    photos.refresh_edit_flag(db, item)


def reject_edit(db: Session, item: Listing, reason: str) -> None:
    """The waiting edit is dropped; the published version stays as it was."""
    item.pending_changes = None
    item.note = reason
    discard_waiting_photos(db, item)
    db.flush()
    photos.refresh_edit_flag(db, item)


def edit(db: Session, item: Listing, changes: dict) -> None:
    """Apply an author's changes according to the state of the listing."""
    if item.status == "draft":
        for key, value in changes.items():
            setattr(item, key, value)
    elif item.status == "approved":
        minor = {k: v for k, v in changes.items() if k not in CRITICAL}
        important = {k: v for k, v in changes.items() if k in CRITICAL}
        for key, value in minor.items():
            setattr(item, key, value)
        if important:
            item.pending_changes = {**(item.pending_changes or {}), **important}
            item.note = ""
        db.flush()
        photos.refresh_edit_flag(db, item)
    else:  # pending, rejected, needs_fix: nothing is published yet, so everything applies and goes to review
        for key, value in changes.items():
            setattr(item, key, value)
        item.status = "pending"
        item.note = ""


RENEW_WINDOW_DAYS = 30   # renewing is possible when this many days or fewer are left (or after the end)
MAX_AHEAD_DAYS = 60      # a listing is never published for more than this many days ahead


def renew_available_from(item: Listing):
    """None when renewing is possible now, otherwise the moment from which it becomes possible."""
    if item.status != "approved" or item.expires_at is None:
        return None
    opens = aware(item.expires_at) - dt.timedelta(days=RENEW_WINDOW_DAYS)
    return opens if opens > now() else None


def renew(item: Listing) -> None:
    """Add the normal lifetime to what is left (so no days are lost), at most MAX_AHEAD_DAYS from now.
    An ended listing starts again from today."""
    current = aware(item.expires_at) if item.expires_at else now()
    base = current if (item.status == "approved" and current > now()) else now()
    extended = base + dt.timedelta(days=settings.listing_ttl_days)
    item.expires_at = min(extended, now() + dt.timedelta(days=MAX_AHEAD_DAYS))
    item.status = "approved"
    item.expiry_notified = False
    item.expiry_notified_1d = False
    item.expiry_notified_7d = False


def expire_listings(db: Session, at: dt.datetime | None = None) -> list[Listing]:
    """Published listings whose time is over stop being shown. Returns them for notifications."""
    moment = at or now()
    expired = [x for x in db.scalars(select(Listing).where(Listing.status == "approved")).all()
               if x.expires_at is not None and aware(x.expires_at) <= moment]
    for item in expired:
        item.status = "expired"
    db.commit()
    return expired


def expiring_week(db: Session, at: dt.datetime | None = None) -> list[Listing]:
    """Listings that end within 7 days (and not yet within 3): the first, early reminder; marks them as warned."""
    moment = at or now()
    soon = [x for x in db.scalars(select(Listing).where(Listing.status == "approved", Listing.expiry_notified_7d.is_(False))).all()
            if x.expires_at is not None and moment + dt.timedelta(days=3) < aware(x.expires_at) <= moment + dt.timedelta(days=7)]
    for item in soon:
        item.expiry_notified_7d = True
    db.commit()
    return soon


def expiring_soon(db: Session, days: int = 3, at: dt.datetime | None = None) -> list[Listing]:
    """Listings that end within `days` and whose author has not been warned yet (marks them as warned)."""
    moment = at or now()
    soon = [x for x in db.scalars(select(Listing).where(Listing.status == "approved", Listing.expiry_notified.is_(False))).all()
            if x.expires_at is not None and moment < aware(x.expires_at) <= moment + dt.timedelta(days=days)]
    for item in soon:
        item.expiry_notified = True
    db.commit()
    return soon


def expiring_tomorrow(db: Session, at: dt.datetime | None = None) -> list[Listing]:
    """Listings that end within a day and whose author has not had the last reminder yet."""
    moment = at or now()
    soon = [x for x in db.scalars(select(Listing).where(Listing.status == "approved", Listing.expiry_notified_1d.is_(False))).all()
            if x.expires_at is not None and moment < aware(x.expires_at) <= moment + dt.timedelta(days=1)]
    for item in soon:
        item.expiry_notified_1d = True
        item.expiry_notified = True  # a listing that jumps straight to "tomorrow" needs no 3-day reminder
    db.commit()
    return soon
