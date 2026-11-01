"""Admin: money reports, VIP history, and the search test («why is this object here / not shown»)."""
import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import catalog, policy
from app.admin_auth import ADM, MOD, staff
from app.database import get_db
from app.models import Listing, Payment, Pricing, User
from app.payments import aware, now

router = APIRouter()


@router.get("/api/admin/reports/money")
def money_report(days: int = Query(default=30, ge=1, le=366), actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    """Revenue, refunds and free grants for the last `days` days, by section and by service."""
    since = now() - dt.timedelta(days=days)
    names = {p.code: p for p in db.scalars(select(Pricing)).all()}
    rows = db.scalars(select(Payment).where(Payment.created_at >= since)).all()
    by_service: dict[str, dict] = {}
    by_section: dict[str, dict] = {}
    payers: set[str] = set()
    for p in rows:
        pr = names.get(p.service_code)
        sec = pr.category if pr else "?"
        for bucket, key in ((by_service, p.service_code), (by_section, sec)):
            row = bucket.setdefault(key, {"title": (pr.title if pr and bucket is by_service else key), "paid": 0, "stars": 0, "from_balance": 0,
                                           "refunded": 0, "refunded_stars": 0, "granted": 0, "saved_stars": 0})
            if p.status == "paid":
                row["paid"] += 1
                row["stars"] += p.stars or 0
                row["from_balance"] += p.balance_used or 0
            elif p.status == "refunded":
                row["refunded"] += 1
                row["refunded_stars"] += (p.stars or 0) + (p.balance_used or 0)
            elif p.status == "granted":
                row["granted"] += 1
                row["saved_stars"] += pr.stars if pr else 0           # what the person would have paid
        if p.status == "paid":
            payers.add(p.user_tg_id)
    total = {k: sum(r[k] for r in by_section.values()) for k in ("paid", "stars", "from_balance", "refunded", "refunded_stars", "granted", "saved_stars")}
    return {"days": days, "total": total | {"payers": len(payers)}, "by_section": by_section, "by_service": by_service}


@router.get("/api/admin/vip/history")
def vip_history(limit: int = Query(default=100, ge=1, le=500), actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    """Every VIP activation: who, when, for how long, how much, paid or given (and by whom, see the note)."""
    vip = {p.code: p for p in db.scalars(select(Pricing).where(Pricing.kind == "vip")).all()}
    if not vip:
        return {"summary": {}, "items": []}
    rows = db.scalars(select(Payment).where(Payment.service_code.in_(list(vip)), Payment.status.in_(["paid", "granted", "refunded"]))
                      .order_by(Payment.id.desc()).limit(limit)).all()
    users = {u.tg_id: u for u in db.scalars(select(User).where(User.tg_id.in_({r.user_tg_id for r in rows}))).all()} if rows else {}
    items = [{"id": r.id, "user": r.user_tg_id, "name": (users.get(r.user_tg_id).display_name if users.get(r.user_tg_id) else "") or "",
              "module": vip[r.service_code].category, "service": vip[r.service_code].title, "days": vip[r.service_code].duration_days,
              "stars": r.stars, "from_balance": r.balance_used, "status": r.status, "note": r.note, "at": str(r.paid_at or r.created_at)} for r in rows]
    paid = [r for r in rows if r.status == "paid"]
    gifts = [r for r in rows if r.status == "granted"]
    return {"summary": {"activations": len(rows), "unique": len({r.user_tg_id for r in rows}), "paid": len(paid), "granted": len(gifts),
                        "stars": sum(r.stars or 0 for r in paid), "saved_stars": sum(vip[r.service_code].stars for r in gifts)},
            "items": items}


def _why_here(i: int, o, kind: str) -> list[str]:
    out = []
    if kind == "pin":
        out.append("Закреплено: отдельная полоса над списком")
    elif kind == "vip":
        out.append("VIP: поднято, но в рамках правила «не больше N платных в окне»")
    elif kind == "bump":
        out.append("Поднято на сутки, правило окна применяется")
    elif kind == "promo":
        out.append("Бесплатная акция: метка есть, подъёма нет")
    else:
        out.append("Обычное объявление: порядок по дате/релевантности")
    if o.published_at:
        out.append(f"Опубликовано {str(o.published_at)[:16]}")
    return out


@router.get("/api/admin/search-test")
def search_test(q: str = Query(default="", max_length=100), category: str | None = None, mode: str = Query(default="people", pattern="^(people|admin)$"),
                actor: str = Depends(staff(*MOD)), db: Session = Depends(get_db)):
    """«As people see it»: the real list with the reason for each place. «As admin sees it»: also what is NOT shown and why."""
    info: dict = {}
    rows, total = catalog.search(db, q=q, category=category, limit=30, _info=info)
    items = catalog.with_promo(db, rows)
    kind = lambda o: "promo" if o.promo_free else (o.promo if o.promo in {"pin", "vip", "bump"} else "")      # noqa: E731
    items = policy.arrange(items, kind)
    out = {"total": total, "corrected": info.get("corrected"),
           "items": [{"place": i + 1, "id": o.id, "title": o.title, "city": o.city, "lane": kind(o) or "organic", "why": _why_here(i, o, kind(o))}
                     for i, o in enumerate(items)]}
    if mode == "admin" and q.strip():
        shown = {o.id for o in items}
        term = f"%{q.strip().lower()}%"
        hidden = db.scalars(select(Listing).where(func.lower(Listing.title).like(term)).order_by(Listing.id.desc()).limit(60)).all()
        reasons = {"pending": "На модерации", "rejected": "Отклонено", "hidden": "Скрыто (жалобы или блокировка)", "draft": "Черновик",
                   "needs_fix": "Возвращено на доработку", "expired": "Срок вышел", "sold": "Закрыто как проданное", "closed": "Закрыто"}
        out["not_shown"] = []
        for x in hidden:
            if x.id in shown:
                continue
            why = reasons.get(x.status) or ("Срок публикации вышел" if x.expires_at and aware(x.expires_at) <= now() else "Не попало в первые 30 по правилам")
            if x.status == "approved" and x.expires_at and aware(x.expires_at) <= now():
                why = "Срок публикации вышел"
            out["not_shown"].append({"id": x.id, "title": x.title, "status": x.status, "why": why})
    return out
