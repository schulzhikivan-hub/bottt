"""Saved forms: a person or a company keeps a ready-made vacancy / listing and starts a new one from it in one tap."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, Integer, String, func, or_, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import company
from app.database import Base, get_db
from app.guard import check_text
from app.payments import now
from app.users import writer

router = APIRouter()
MAX_TEMPLATES = 20
KEEP = {"category", "title", "region_code", "district_id", "price", "description", "contact", "price_amount", "salary_period", "employment",
        "schedule", "housing_provided", "meals_provided", "visas", "rooms", "condition", "housing_type", "deposit", "area_m2", "pets_allowed",
        "furnished", "goods_category", "negotiable", "year", "mileage_km", "details"}


class ListingTemplate(Base):
    __tablename__ = "listing_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_tg_id: Mapped[str] = mapped_column(String(40), index=True)
    company_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(60))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class TemplateIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    data: dict
    company_id: int | None = None


def _out(t: ListingTemplate) -> dict:
    return {"id": t.id, "name": t.name, "company_id": t.company_id, "data": t.data, "category": t.data.get("category", "")}


@router.get("/api/me/templates")
def mine(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    team = [m.company_id for m in db.scalars(select(company.CompanyMember).where(
        company.CompanyMember.tg_id == user_id, company.CompanyMember.role.in_(company.WRITE))).all()]
    rows = db.scalars(select(ListingTemplate).where(or_(ListingTemplate.owner_tg_id == user_id, ListingTemplate.company_id.in_(team or [0])))
                      .order_by(ListingTemplate.id.desc()).limit(60)).all()
    return [_out(t) for t in rows]


@router.post("/api/me/templates", status_code=201)
def create(p: TemplateIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    data = {k: v for k, v in p.data.items() if k in KEEP}
    if not data.get("category"):
        raise HTTPException(400, "В шаблоне нет раздела")
    check_text(str(data.get("title", "")), str(data.get("description", "")))
    if p.company_id and company.role_of(db, p.company_id, user_id) not in company.WRITE:
        raise HTTPException(403, "Шаблоны компании могут сохранять сотрудники с правом публиковать")
    count = db.scalar(select(func.count()).select_from(ListingTemplate).where(ListingTemplate.owner_tg_id == user_id)) or 0
    if count >= MAX_TEMPLATES:
        raise HTTPException(409, f"Шаблонов уже {MAX_TEMPLATES}: удалите ненужные")
    t = ListingTemplate(owner_tg_id=user_id, company_id=p.company_id, name=p.name.strip(), data=data)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _out(t)


@router.delete("/api/me/templates/{template_id}")
def remove(template_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    t = db.get(ListingTemplate, template_id)
    if t is None or (t.owner_tg_id != user_id and company.role_of(db, t.company_id, user_id) not in company.TEAM):
        raise HTTPException(404, "Шаблон не найден")
    db.delete(t)
    db.commit()
    return {"ok": True}
