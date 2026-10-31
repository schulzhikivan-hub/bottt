"""Saved searches: the user keeps a set of filters and the bot tells them about new matching listings."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app import catalog, fields
from app.database import get_db
from app.models import SavedSearch, User
from app.payments import now, aware
from app.users import writer
from typing import Literal

router = APIRouter()
MAX_SEARCHES = 10


class SearchIn(BaseModel):
    name: str = Field(default="", max_length=80)
    q: str = Field(default="", max_length=100)
    category: Literal["job", "housing", "marketplace"] | None = None
    region: str | None = Field(default=None, max_length=20)
    district_id: int | None = None
    price_min: int | None = Field(default=None, ge=0)
    price_max: int | None = Field(default=None, ge=0)
    housing_provided: bool | None = None
    meals_provided: bool | None = None
    condition: Literal["new", "used"] | None = None
    rooms_min: int | None = Field(default=None, ge=0, le=20)
    visa: fields.VisaCode | None = None
    employment: fields.Employment | None = None
    schedule: fields.Schedule | None = None
    housing_type: fields.HousingType | None = None
    deposit_max: int | None = Field(default=None, ge=0)
    area_min: int | None = Field(default=None, ge=0)
    pets_allowed: bool | None = None
    furnished: bool | None = None
    goods_category: fields.GoodsCategory | None = None


def _params(payload: SearchIn) -> dict:
    data = payload.model_dump(exclude={"name"}, exclude_none=True)
    return {k: v for k, v in data.items() if v not in ("", False)}


def _title(payload: SearchIn, params: dict) -> str:
    if payload.name.strip():
        return payload.name.strip()
    names = {"job": "Работа", "housing": "Жильё", "marketplace": "Барахолка"}
    bits = [names.get(params.get("category"), "Всё"), params.get("q", "")]
    return " · ".join(b for b in bits if b)[:80]


@router.post("/api/me/searches", status_code=201)
def save_search(payload: SearchIn, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    params = _params(payload)
    if not params:
        raise HTTPException(400, "Задайте слово или фильтр, за которым следить")
    count = db.scalar(select(func.count()).select_from(SavedSearch).where(SavedSearch.user_tg_id == user_id)) or 0
    if count >= MAX_SEARCHES:
        raise HTTPException(409, f"Не больше {MAX_SEARCHES} сохранённых поисков")
    row = SavedSearch(user_tg_id=user_id, name=_title(payload, params), params=params, last_checked_at=now())
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name}


@router.get("/api/me/searches")
def my_searches(user_id: str = Depends(writer), db: Session = Depends(get_db)):
    rows = db.scalars(select(SavedSearch).where(SavedSearch.user_tg_id == user_id).order_by(SavedSearch.id.desc())).all()
    return [{"id": r.id, "name": r.name, "params": r.params} for r in rows]


@router.delete("/api/me/searches/{search_id}")
def delete_search(search_id: int, user_id: str = Depends(writer), db: Session = Depends(get_db)):
    row = db.get(SavedSearch, search_id)
    if row is None or row.user_tg_id != user_id:
        raise HTTPException(404, "Поиск не найден")
    db.delete(row)
    db.commit()
    return {"ok": True}


def new_matches(db: Session) -> list[tuple[str, str]]:
    """For every saved search: listings published since the last check. Returns (user_id, message) pairs."""
    messages = []
    for row in db.scalars(select(SavedSearch)).all():
        since = aware(row.last_checked_at)
        row.last_checked_at = now()
        user = db.get(User, row.user_tg_id)
        if user is None or user.blocked or user.deleted_at is not None:
            continue
        items, _ = catalog.search(db, limit=50, **row.params)
        fresh = [x for x in items if x.published_at and aware(x.published_at) > since
                 and x.author_telegram_id != row.user_tg_id]
        if fresh:
            first = fresh[0]
            more = f" и ещё {len(fresh) - 1}" if len(fresh) > 1 else ""
            messages.append((row.user_tg_id, f"🔔 Новое по вашему поиску «{row.name}»: {first.title} — {first.city}{more}. "
                                             "Откройте приложение, чтобы посмотреть."))
    db.commit()
    return messages
