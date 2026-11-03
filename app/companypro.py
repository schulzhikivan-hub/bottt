"""«Про» for a company: a monthly plan paid from the COMPANY balance in KH. It lifts the limits (more active listings and
a bigger team) and shows a badge on the public page."""
import datetime as dt
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import DateTime, Integer
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import company, rules
from app.audit import log_action
from app.database import Base, get_db
from app.models import Payment
from app.payments import aware, now
from app.users import writer

router = APIRouter()
CODE = "company_pro"
DAYS = 30


class CompanyPlan(Base):
    __tablename__ = "company_plans"
    company_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    until: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


def until(db: Session, company_id: int | None):
    row = db.get(CompanyPlan, company_id) if company_id else None
    return aware(row.until) if row is not None and aware(row.until) > now() else None


def is_pro(db: Session, company_id: int | None) -> bool:
    return until(db, company_id) is not None


def factor(db: Session, company_id: int | None) -> int:
    """How many times the listing limits are multiplied (1 without «Про»)."""
    return rules.val("pro.limit_x") if is_pro(db, company_id) else 1


def extra_members(db: Session, company_id: int | None) -> int:
    return rules.val("pro.extra_members") if is_pro(db, company_id) else 0


def info(db: Session, company_id: int) -> dict:
    u = until(db, company_id)
    return {"active": u is not None, "until": u.isoformat() if u else None, "price": rules.val("pro.price"), "days": DAYS,
            "perks": [f"Объявлений в разделах — в {rules.val('pro.limit_x')} раза больше", f"В команде на {rules.val('pro.extra_members')} человек больше",
                      "Значок «Про» на публичной странице"]}


@router.get("/api/company/{company_id}/pro")
def status(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    c, role = company._company(db, company_id, user_id)
    return info(db, c.id)


@router.post("/api/company/{company_id}/pro")
def buy(company_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    """30 days of «Про» (added to what is left) for the price in rules, from the company balance."""
    c, role = company._company(db, company_id, user_id, company.MONEY)
    if c.status != "verified":
        raise HTTPException(409, "Подписка доступна проверенной компании")
    price = rules.val("pro.price")
    have = c.balance_stars or 0
    if have < price:
        raise HTTPException(400, f"Недостаточно KH на балансе компании: нужно {price}, есть {have}. Пополните баланс компании")
    c.balance_stars = have - price
    base = until(db, c.id) or now()
    row = db.get(CompanyPlan, c.id)
    if row is None:
        db.add(CompanyPlan(company_id=c.id, until=base + dt.timedelta(days=DAYS)))
    else:
        row.until = base + dt.timedelta(days=DAYS)
    token = secrets.token_urlsafe(10)                      # unique even for two purchases in one second
    db.add(Payment(user_tg_id=user_id, listing_id=None, service_code=CODE, stars=0, list_stars=price, balance_used=price,
                   payload=f"pro-{c.id}-{token}", status="paid", created_at=now(), paid_at=now(),
                   telegram_payment_charge_id=f"balance-pro-{c.id}-{token}", company_id=c.id, note="Подписка «Про»"))
    log_action(db, user_id, "company.pro", "company", c.id, f"{price} KH, +{DAYS} дн.")
    db.commit()
    return info(db, c.id)
