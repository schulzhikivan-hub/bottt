"""KH: the site's own currency. Balances and prices are in KH; Telegram Stars are how people buy KH.
The exchange rate is set by the owner («обменник»): how many KH one Star gives."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import payments, rules
from app.admin_auth import ADM, FIN, staff
from app.company import Company
from app.database import get_db
from app.models import Payment, User
from app.payments import now

router = APIRouter()


def public_view() -> dict:
    rate = payments.kh_per_star()
    return {"currency": "KH", "kh_per_star": rate, "balance_only": payments.balance_only(),
            "min": payments.TOPUP_MIN, "max": payments.TOPUP_MAX, "example": {"stars": 100, "kh": round(100 * rate)}}


@router.get("/api/exchange")
def exchange():
    return public_view()


@router.get("/api/admin/exchange")
def admin_exchange(actor: str = Depends(staff(*FIN)), db: Session = Depends(get_db)):
    people = db.scalar(select(func.coalesce(func.sum(User.balance_stars), 0))) or 0
    companies = db.scalar(select(func.coalesce(func.sum(Company.balance_stars), 0))) or 0
    since = now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.scalars(select(Payment).where(Payment.service_code == payments.TOPUP_CODE, Payment.status == "paid",
                                            Payment.paid_at >= since.replace(day=1))).all()
    spent_rows = db.execute(select(Payment.service_code, func.coalesce(func.sum(Payment.balance_used), 0), func.count()).where(
        Payment.status == "paid", Payment.service_code != payments.TOPUP_CODE, Payment.paid_at >= since.replace(day=1), Payment.balance_used > 0)
        .group_by(Payment.service_code).order_by(func.sum(Payment.balance_used).desc())).all()
    spent = {"kh": int(sum(r[1] for r in spent_rows)), "by_service": [{"code": r[0], "kh": int(r[1]), "n": r[2]} for r in spent_rows[:8]]}
    return {**public_view(), "spent_month": spent, "per_100_stars": rules.val("kh.per_100_stars"), "in_circulation": {"people": int(people), "companies": int(companies),
            "total": int(people + companies)},
            "month": {"topups": len(rows), "stars": sum(p.stars for p in rows), "kh": sum(p.list_stars or p.stars for p in rows)}}


class ExchangeIn(BaseModel):
    per_100_stars: int = Field(ge=10, le=1000)
    balance_only: bool


@router.put("/api/admin/exchange")
def set_exchange(p: ExchangeIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    if p.per_100_stars != 100 and not p.balance_only:
        raise HTTPException(400, "Курс не 1:1 возможен только когда услуги оплачиваются с баланса KH: включите «Услуги только за KH»")
    rules.set_value(db, "kh.per_100_stars", p.per_100_stars, actor)
    rules.set_value(db, "kh.balance_only", p.balance_only, actor)
    rules.reset_cache()
    return admin_exchange(actor, db)


def _series(db: Session, days: int) -> list[dict]:
    import datetime as dt
    start = (now() - dt.timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.scalars(select(Payment).where(Payment.status == "paid", Payment.paid_at >= start)).all()
    out = {(start + dt.timedelta(days=i)).date().isoformat(): {"day": (start + dt.timedelta(days=i)).date().isoformat(), "stars_in": 0, "kh_bought": 0,
                                                              "kh_spent": 0, "purchases": 0} for i in range(days)}
    for p in rows:
        d = out.get(p.paid_at.date().isoformat())
        if d is None:
            continue
        if p.service_code == payments.TOPUP_CODE:
            d["stars_in"] += p.stars or 0
            d["kh_bought"] += p.list_stars or p.stars or 0
        elif (p.balance_used or 0) > 0:
            d["kh_spent"] += p.balance_used
            d["purchases"] += 1
    return list(out.values())


@router.get("/api/admin/exchange/report")
def report(days: int = 30, actor: str = Depends(staff(*FIN)), db: Session = Depends(get_db)):
    """Day by day: Stars received, KH bought, KH spent on services (a picture of the KH economy)."""
    days = max(7, min(days, 180))
    rows = _series(db, days)
    return {"days": rows, "total": {k: sum(r[k] for r in rows) for k in ("stars_in", "kh_bought", "kh_spent", "purchases")}}


@router.get("/api/admin/exchange/report.csv")
def report_csv(days: int = 90, actor: str = Depends(staff(*FIN)), db: Session = Depends(get_db)):
    import csv
    import io
    from fastapi.responses import Response
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["День", "Получено звёзд", "Куплено KH", "Потрачено KH", "Покупок услуг"])
    for r in _series(db, max(7, min(days, 365))):
        w.writerow([r["day"], r["stars_in"], r["kh_bought"], r["kh_spent"], r["purchases"]])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="kh-report.csv"'})
