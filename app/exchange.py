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
