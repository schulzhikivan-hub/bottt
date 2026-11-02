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


# ---------- rebuild of the search text, with versions ----------
from sqlalchemy import JSON, update      # noqa: E402

KEEP_VERSIONS = 5


class IndexVersion(Base):
    """What a reindex changed: the old text of every changed listing, so the change can be taken back."""
    __tablename__ = "search_index_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    by: Mapped[str] = mapped_column(String(40), default="")
    changed: Mapped[int] = mapped_column(Integer, default=0)
    rolled_back: Mapped[bool] = mapped_column(default=False)
    data: Mapped[dict] = mapped_column(JSON, default=dict)          # {listing_id: [old search_text, old city_key]}


def canonical(item: Listing) -> tuple[str, str]:
    """The text and the city key a listing must be found by: the same rule as when it is saved."""
    extra = [str(v) for k, v in (item.details or {}).items() if k in ("company", "position", "station", "brand")]
    return (" ".join([item.title or "", item.description or "", item.city or "", *extra]).lower(), (item.city or "").strip().lower())


@router.post("/api/admin/search/reindex")
def reindex(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    """Rebuild the text every listing is found by. The old texts of the changed listings are kept as a version: a mistake in the rule
    can be taken back with one click. Plain UPDATEs, so a listing is never without text while it is rebuilt."""
    old: dict[str, list] = {}
    todo: list[tuple[int, str, str]] = []
    for item in db.scalars(select(Listing)).all():
        text, key = canonical(item)
        if item.search_text != text or item.city_key != key:
            old[str(item.id)] = [item.search_text, item.city_key]
            todo.append((item.id, text, key))
    for lid, text, key in todo:
        db.execute(update(Listing).where(Listing.id == lid).values(search_text=text, city_key=key))
    version = None
    if todo:
        version = IndexVersion(by=actor, changed=len(todo), data=dict(old))
        db.add(version)
        db.flush()
        stale = db.scalars(select(IndexVersion.id).order_by(IndexVersion.id.desc()).offset(KEEP_VERSIONS)).all()
        if stale:
            db.execute(delete(IndexVersion).where(IndexVersion.id.in_(stale)))
    log_action(db, actor, "search.reindex", "search", "all", f"обновлено {len(todo)}" + (f", версия #{version.id}" if version else ""))
    db.commit()
    return {"updated": len(todo), "version": version.id if version else None}


@router.get("/api/admin/search/index-versions")
def index_versions(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    total = db.scalar(select(func.count()).select_from(Listing)) or 0
    stale = sum(1 for x in db.scalars(select(Listing)).all() if (x.search_text, x.city_key) != canonical(x))
    rows = db.scalars(select(IndexVersion).order_by(IndexVersion.id.desc())).all()
    return {"listings": total, "out_of_date": stale, "versions": [{"id": v.id, "at": v.at.isoformat(), "by": "…" + v.by[-4:], "changed": v.changed,
                                                                  "rolled_back": v.rolled_back} for v in rows]}


@router.post("/api/admin/search/index-versions/{version_id}/rollback")
def index_rollback(version_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    """Put the old search texts of that reindex back (only for listings that still exist)."""
    v = db.get(IndexVersion, version_id)
    if v is None:
        raise HTTPException(404, "Версия не найдена")
    if v.rolled_back:
        raise HTTPException(409, "Эта версия уже откатывалась")
    n = 0
    for lid, (text, key) in v.data.items():
        n += db.execute(update(Listing).where(Listing.id == int(lid)).values(search_text=text, city_key=key)).rowcount or 0
    v.rolled_back = True
    log_action(db, actor, "search.reindex_rollback", "search", str(version_id), f"возвращено {n}")
    db.commit()
    return {"restored": n}
