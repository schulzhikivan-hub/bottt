"""What people search for (to improve search), kept without personal data and not for long.

- before saving, the text is cleaned: e-mails, phone-like numbers (6+ digits) and @names are replaced by marks;
- only the cleaned text, the number of results and the day are kept - no person, no address;
- kept 90 days (Rules → «Хранение данных»);
- looking at the log is written to the journal; the export is for the super administrator only."""
import csv
import datetime as dt
import io
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import DateTime, Integer, String, delete, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.admin_auth import ADM, SUP, staff
from app.audit import log_action
from app.database import Base, get_db
from app.models import Listing, _now
from app.payments import now

router = APIRouter()
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
HANDLE = re.compile(r"@\w{3,}")
NUMBER = re.compile(r"\+?\d[\d\s().-]{5,}\d")


class SearchLog(Base):
    __tablename__ = "search_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    q: Mapped[str] = mapped_column(String(100))
    results: Mapped[int] = mapped_column(Integer, default=0)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


def clean(q: str) -> str:
    q = EMAIL.sub("<email>", q or "")
    q = HANDLE.sub("<имя>", q)
    q = NUMBER.sub("<номер>", q)
    return " ".join(q.lower().split())[:100]


def record(db: Session, q: str, results: int) -> None:
    text = clean(q)
    if len(text) < 2:
        return
    db.add(SearchLog(q=text, results=results))
    db.commit()


def purge(db: Session, days: int) -> int:
    n = db.execute(delete(SearchLog).where(SearchLog.at < now() - dt.timedelta(days=days))).rowcount or 0
    db.commit()
    return n


@router.get("/api/admin/search-log")
def view(days: int = Query(default=7, ge=1, le=90), actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    since = now() - dt.timedelta(days=days)
    top = db.execute(select(SearchLog.q, func.count(), func.min(SearchLog.results)).where(SearchLog.at >= since).group_by(SearchLog.q)
                     .order_by(func.count().desc()).limit(30)).all()
    empty = db.execute(select(SearchLog.q, func.count()).where(SearchLog.at >= since, SearchLog.results == 0).group_by(SearchLog.q)
                       .order_by(func.count().desc()).limit(30)).all()
    total = db.scalar(select(func.count()).select_from(SearchLog).where(SearchLog.at >= since)) or 0
    log_action(db, actor, "searchlog.view", "search", "log", f"{days} дн.")
    db.commit()
    return {"days": days, "total": total, "top": [{"q": q, "n": n, "min_results": r} for q, n, r in top],
            "empty": [{"q": q, "n": n} for q, n in empty]}


@router.get("/api/admin/search-log/export")
def export(days: int = Query(default=90, ge=1, le=90), actor: str = Depends(staff(*SUP)), db: Session = Depends(get_db)):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["день", "запрос", "найдено"])
    for r in db.scalars(select(SearchLog).where(SearchLog.at >= now() - dt.timedelta(days=days)).order_by(SearchLog.id)).all():
        w.writerow([r.at.date().isoformat(), r.q, r.results])
    log_action(db, actor, "searchlog.export", "search", "log", f"{days} дн.")
    db.commit()
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=search-log.csv"})


# ---------- rebuild of the search text ----------
@router.post("/api/admin/search/reindex")
def reindex(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    """The search reads the database directly; «reindex» rebuilds the text each listing is found by (title, description, city)
    for all listings, in pieces. Safe to run on a live site: a listing is never without text while it is rebuilt."""
    n = 0
    for item in db.scalars(select(Listing)).all():
        text = " ".join([item.title or "", item.description or "", item.city or ""]).lower()
        if item.search_text != text:
            item.search_text = text
            n += 1
    log_action(db, actor, "search.reindex", "search", "all", f"обновлено {n}")
    db.commit()
    return {"updated": n}
