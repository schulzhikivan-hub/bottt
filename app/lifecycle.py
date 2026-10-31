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
    show_waiting_photos(db, item)
    db.flush()
    photos.refresh_edit_flag(db, item)


def apply_edit(db: Session, item: Listing) -> None:
    """A moderator approved the waiting edit: the new version replaces the old one."""
    for key, value in (item.pending_changes or {}).items():
        setattr(item, key, value)
    item.pending_changes = None
    item.note = ""
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


def renew(item: Listing) -> None:
    item.status = "approved"
    item.expires_at = now() + dt.timedelta(days=settings.listing_ttl_days)
    item.expiry_notified = False


def expire_listings(db: Session, at: dt.datetime | None = None) -> list[Listing]:
    """Published listings whose time is over stop being shown. Returns them for notifications."""
    moment = at or now()
    expired = [x for x in db.scalars(select(Listing).where(Listing.status == "approved")).all()
               if x.expires_at is not None and aware(x.expires_at) <= moment]
    for item in expired:
        item.status = "expired"
    db.commit()
    return expired


def expiring_soon(db: Session, days: int = 3, at: dt.datetime | None = None) -> list[Listing]:
    """Listings that end within `days` and whose author has not been warned yet (marks them as warned)."""
    moment = at or now()
    soon = [x for x in db.scalars(select(Listing).where(Listing.status == "approved", Listing.expiry_notified.is_(False))).all()
            if x.expires_at is not None and moment < aware(x.expires_at) <= moment + dt.timedelta(days=days)]
    for item in soon:
        item.expiry_notified = True
    db.commit()
    return soon
