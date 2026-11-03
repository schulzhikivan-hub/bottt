"""How long data is kept. Run once a day by the bot. Money records, listings and chats are never touched here.

- notices in the feed: 90 days
- who-viewed marks of dating profiles: 90 days
- closed support requests: 12 months
- finished promotion claims: 12 months"""
import datetime as dt

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import rules
from app.models import DatingView, Heartbeat, Notification, Ticket, TicketMessage
from app.payments import aware, now

NOTICE_DAYS = 90
VIEW_DAYS = 90
TICKET_DAYS = 365


def purge(db: Session, at: dt.datetime | None = None) -> dict:
    at = at or now()
    out = {}
    out["notifications"] = db.execute(delete(Notification).where(Notification.created_at < at - dt.timedelta(days=rules.val("keep.notice_days")))).rowcount or 0
    out["views"] = db.execute(delete(DatingView).where(DatingView.at < at - dt.timedelta(days=rules.val("keep.view_days")))).rowcount or 0
    from app import searchlog
    from app import viewstats
    viewstats.purge(db)                # day-by-day listing views older than four months
    out["searches"] = searchlog.purge(db, rules.val("keep.search_days"))
    old = db.scalars(select(Ticket).where(Ticket.status == "closed", Ticket.updated_at < at - dt.timedelta(days=rules.val("keep.ticket_days")))).all()
    for t in old:
        db.execute(delete(TicketMessage).where(TicketMessage.ticket_id == t.id))
        db.delete(t)
    out["tickets"] = len(old)
    db.commit()
    return out


def run_daily(db: Session) -> dict | None:
    """Once in 24 hours, remembered in the heartbeat table, so two bot restarts do not run it twice."""
    mark = db.get(Heartbeat, "retention")
    if mark is not None and aware(mark.at) > now() - dt.timedelta(hours=24):
        return None
    result = purge(db)
    if mark is None:
        db.add(Heartbeat(name="retention", at=now()))
    else:
        mark.at = now()
    db.commit()
    return result
