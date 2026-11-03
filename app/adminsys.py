"""Server load for the dashboard (no extra libraries: /proc and the standard library) and the reset of app errors."""
import os
import shutil
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.admin_auth import ADM, staff
from app.audit import log_action
from app.config import settings
from app.database import engine, get_db
from app.models import ClientError
from app.payments import now

router = APIRouter()
_BOOT = time.time()


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _meminfo() -> dict:
    out = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        parts = v.split()
        if parts and parts[0].isdigit():
            out[k] = int(parts[0]) * 1024
    return out


def level(pct: float | None, warn: float, bad: float) -> str:
    if pct is None:
        return "na"
    return "bad" if pct >= bad else "warn" if pct >= warn else "ok"


def collect(db_size: int | None = None) -> dict:
    cpus = os.cpu_count() or 1
    try:
        l1, l5, l15 = os.getloadavg()
    except OSError:
        l1 = l5 = l15 = 0.0
    mi = _meminfo()
    total, avail = mi.get("MemTotal"), mi.get("MemAvailable")
    limit = _read("/sys/fs/cgroup/memory.max").strip()          # a container may have a smaller limit than the machine
    used_cg = _read("/sys/fs/cgroup/memory.current").strip()
    if limit.isdigit() and used_cg.isdigit() and (total is None or int(limit) < total):
        total, avail = int(limit), int(limit) - int(used_cg)
    mem_pct = round(100 * (total - avail) / total, 1) if total and avail is not None else None
    path = settings.upload_dir if os.path.exists(settings.upload_dir) else "."
    try:
        du = shutil.disk_usage(path)
        disk_pct, disk = round(100 * du.used / du.total, 1), {"total": du.total, "used": du.used, "free": du.free}
    except OSError:
        disk_pct, disk = None, {}
    rss = None
    for line in _read("/proc/self/status").splitlines():
        if line.startswith("VmRSS:"):
            rss = int(line.split()[1]) * 1024
    up = _read("/proc/uptime").split()
    load_pct = round(100 * l1 / cpus, 1)
    return {"cpu": {"cores": cpus, "load1": round(l1, 2), "load5": round(l5, 2), "load15": round(l15, 2), "pct": load_pct, "level": level(load_pct, 80, 150)},
            "memory": {"total": total, "available": avail, "pct": mem_pct, "level": level(mem_pct, 85, 95), "app_rss": rss},
            "disk": {**disk, "pct": disk_pct, "level": level(disk_pct, 85, 95)},
            "uptime_s": int(float(up[0])) if up else int(time.time() - _BOOT), "app_uptime_s": int(time.time() - _BOOT), "db_bytes": db_size}


@router.get("/api/admin/system/metrics")
def metrics(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    size = None
    try:
        if engine.url.drivername.startswith("postgresql"):
            size = db.execute(text("select pg_database_size(current_database())")).scalar()
        elif engine.url.database and os.path.exists(engine.url.database):
            size = os.path.getsize(engine.url.database)
    except Exception:  # noqa: BLE001
        size = None
    return collect(size)


# ---------- errors from the app: look, group, reset ----------
@router.get("/api/admin/client-errors/groups")
def groups(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    """The same problem seen by many people is one line with a count (and the people it touched)."""
    rows = db.execute(select(ClientError.where, ClientError.status, ClientError.message, func.count(), func.count(func.distinct(ClientError.user_tg_id)),
                             func.max(ClientError.created_at), func.max(ClientError.request_id))
                      .group_by(ClientError.where, ClientError.status, ClientError.message).order_by(func.max(ClientError.created_at).desc()).limit(100)).all()
    return [{"where": w, "status": s, "message": m, "count": n, "people": p, "last": last.isoformat() if last else None, "request_id": rid or ""}
            for w, s, m, n, p, last, rid in rows]


class ClearIn(BaseModel):
    mode: str = Field(pattern="^(all|group|older)$")
    where: str = Field(default="", max_length=120)
    status: int | None = None
    message: str = Field(default="", max_length=300)
    days: int = Field(default=7, ge=1, le=365)


@router.post("/api/admin/client-errors/clear")
def clear(p: ClearIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    """Reset the list: everything, one group (same place, status and message) or what is older than N days."""
    stmt = delete(ClientError)
    if p.mode == "group":
        if not p.where and not p.message:
            raise HTTPException(400, "Не указана группа")
        stmt = stmt.where(ClientError.where == p.where, ClientError.message == p.message, ClientError.status == (p.status or 0))
    elif p.mode == "older":
        import datetime as dt
        stmt = stmt.where(ClientError.created_at < now() - dt.timedelta(days=p.days))
    n = db.execute(stmt).rowcount or 0
    log_action(db, actor, "errors.clear", "errors", p.mode, f"удалено {n}")
    db.commit()
    return {"deleted": n}


@router.delete("/api/admin/client-errors/{error_id}")
def delete_one(error_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    row = db.get(ClientError, error_id)
    if row is None:
        raise HTTPException(404, "Ошибка уже удалена")
    db.delete(row)
    db.commit()
    return {"deleted": 1}


# ---------- «suspicious» block of the dashboard ----------
@router.get("/api/admin/suspicious")
def suspicious(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    """Three things a person would not notice alone: people with several open complaints, a burst of listings from one account in an
    hour, and one contact used by different accounts (the contact itself is hidden)."""
    import datetime as dt
    from app.models import Listing, Report, User
    people = db.execute(select(Report.target_id, func.count(func.distinct(Report.reporter_tg_id))).where(Report.target_type == "user", Report.status == "new")
                        .group_by(Report.target_id).having(func.count(func.distinct(Report.reporter_tg_id)) >= 2)
                        .order_by(func.count(func.distinct(Report.reporter_tg_id)).desc()).limit(5)).all()
    names = {u.tg_id: u.display_name for u in db.scalars(select(User).where(User.tg_id.in_([p[0] for p in people] or [""]))).all()}
    burst = db.execute(select(Listing.author_telegram_id, func.count()).where(Listing.created_at >= now() - dt.timedelta(hours=1), Listing.company_id.is_(None))
                       .group_by(Listing.author_telegram_id).having(func.count() >= 5).order_by(func.count().desc()).limit(5)).all()
    shared = db.execute(select(Listing.contact, func.count(func.distinct(Listing.author_telegram_id))).where(Listing.contact != "", Listing.status.in_(["pending", "approved"]))
                        .group_by(Listing.contact).having(func.count(func.distinct(Listing.author_telegram_id)) >= 2)
                        .order_by(func.count(func.distinct(Listing.author_telegram_id)).desc()).limit(5)).all()

    def mask(c: str) -> str:
        c = c.strip()
        return (c[:3] + "•••" + c[-2:]) if len(c) > 6 else "•••"
    return {"reported": [{"tg_id": t, "name": names.get(t, ""), "reports": n} for t, n in people],
            "burst": [{"tg_id": t, "listings": n} for t, n in burst],
            "shared_contact": [{"contact": mask(c), "accounts": n} for c, n in shared]}
