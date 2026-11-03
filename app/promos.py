"""Promo codes for paid services: created and switched off by administrators (web API; the bot has its own screen)."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin_auth import staff, ADM
from app.audit import log_action
from app.database import get_db
from app.models import PromoCode
from app.payments import now, normalize_code

router = APIRouter()


class PromoIn(BaseModel):
    code: str = Field(min_length=3, max_length=24)
    percent: int | None = Field(default=None, ge=1, le=90)
    fixed_stars: int | None = Field(default=None, ge=1, le=10000)
    max_uses: int | None = Field(default=None, ge=1, le=100000)
    days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def one_kind_of_discount(self):
        if (self.percent is None) == (self.fixed_stars is None):
            raise ValueError("Укажите либо процент скидки, либо число звёзд")
        return self


def view(p: PromoCode) -> dict:
    return {"id": p.id, "code": p.code, "percent": p.percent, "fixed_stars": p.fixed_stars, "max_uses": p.max_uses,
            "used": p.used_count, "valid_until": str(p.valid_until) if p.valid_until else None, "active": p.is_active}


def create_promo(db: Session, actor: str, payload: PromoIn) -> PromoCode:
    code = normalize_code(payload.code)
    if not code.isalnum():
        raise HTTPException(400, "Код: только буквы и цифры")
    if db.scalar(select(PromoCode.id).where(PromoCode.code == code)):
        raise HTTPException(409, "Такой код уже есть")
    promo = PromoCode(code=code, percent=payload.percent, fixed_stars=payload.fixed_stars, max_uses=payload.max_uses,
                      valid_until=now() + dt.timedelta(days=payload.days) if payload.days else None)
    db.add(promo)
    log_action(db, actor, "promo.create", "promo", code, f"{payload.percent or payload.fixed_stars}")
    db.commit()
    db.refresh(promo)
    return promo


@router.get("/api/admin/promos")
def list_promos(actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return [view(p) for p in db.scalars(select(PromoCode).order_by(PromoCode.id.desc())).all()]


@router.post("/api/admin/promos", status_code=201)
def add_promo(payload: PromoIn, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    return view(create_promo(db, actor, payload))


@router.put("/api/admin/promos/{promo_id}/toggle")
def toggle_promo(promo_id: int, actor: str = Depends(staff(*ADM)), db: Session = Depends(get_db)):
    promo = db.get(PromoCode, promo_id)
    if promo is None:
        raise HTTPException(404, "Промокод не найден")
    promo.is_active = not promo.is_active
    log_action(db, actor, "promo.toggle", "promo", promo.code, f"active={promo.is_active}")
    db.commit()
    return view(promo)
