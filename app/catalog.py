"""Browsing and search of published listings: words, filters, sorting, pages, details, cities."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, case, or_
from sqlalchemy.orm import Session

from app import payments
from app.database import get_db
from app.models import Listing, Placement
from app.schemas import CATEGORIES, ListingOut

router = APIRouter()

# Extra details belong to one section only: a job has housing and meals, housing has rooms, goods have a condition
CATEGORY_EXTRAS = {
    "job": {"housing_provided", "meals_provided"},
    "housing": {"rooms"},
    "marketplace": {"condition"},
}
ALL_EXTRAS = set().union(*CATEGORY_EXTRAS.values())
MAX_WORDS = 6


def clean_extras(category: str, data: dict) -> dict:
    """Drop details that do not fit the section (for example rooms on a vacancy)."""
    allowed = CATEGORY_EXTRAS.get(category, set())
    return {key: (None if key in ALL_EXTRAS and key not in allowed else value) for key, value in data.items()}


def drop_irrelevant(category: str, changes: dict) -> dict:
    allowed = CATEGORY_EXTRAS.get(category, set())
    return {key: value for key, value in changes.items() if key not in ALL_EXTRAS or key in allowed}


def _like(word: str) -> str:
    escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _promo_rank():
    rank = case((Placement.kind == "pin", 4), (Placement.kind == "vip", 3),
                (Placement.kind == "bump", 2), (Placement.kind == "highlight", 1), else_=0)
    return (select(func.coalesce(func.max(rank), 0))
            .where(Placement.listing_id == Listing.id, Placement.status == "active")
            .correlate(Listing).scalar_subquery())


def search(db: Session, *, q: str = "", category: str | None = None, city: str | None = None,
           price_min: int | None = None, price_max: int | None = None, sort: str = "new",
           housing_provided: bool | None = None, meals_provided: bool | None = None,
           condition: str | None = None, rooms_min: int | None = None,
           limit: int = 20, offset: int = 0) -> tuple[list[Listing], int]:
    """Published listings matching every condition. Paid placements come first on every page."""
    conditions = [Listing.status == "approved"]
    if category:
        conditions.append(Listing.category == category)
    if city and city.strip():
        conditions.append(Listing.city_key.like(_like(city.strip().lower()), escape="\\"))
    # Every word must be found in the title, description or city (case is ignored)
    for word in q.lower().split()[:MAX_WORDS]:
        conditions.append(Listing.search_text.like(_like(word), escape="\\"))
    if price_min is not None:
        conditions.append(Listing.price_amount >= price_min)
    if price_max is not None:
        conditions.append(Listing.price_amount <= price_max)
    if housing_provided:
        conditions.append(Listing.housing_provided.is_(True))
    if meals_provided:
        conditions.append(Listing.meals_provided.is_(True))
    if condition:
        conditions.append(Listing.condition == condition)
    if rooms_min is not None:
        conditions.append(Listing.rooms >= rooms_min)

    total = db.scalar(select(func.count()).select_from(Listing).where(*conditions)) or 0
    if sort == "price_asc":
        order = [Listing.price_amount.asc().nulls_last(), Listing.id.desc()]
    elif sort == "price_desc":
        order = [Listing.price_amount.desc().nulls_last(), Listing.id.desc()]
    else:
        order = [Listing.id.desc()]
    rows = db.scalars(select(Listing).where(*conditions)
                      .order_by(_promo_rank().desc(), *order).limit(limit).offset(offset)).all()
    return list(rows), total


def with_promo(db: Session, rows: list[Listing]) -> list[ListingOut]:
    placed = payments.active_placements(db, [x.id for x in rows])
    return [ListingOut.model_validate(x).model_copy(update={"promo": placed.get(x.id), "note": ""}) for x in rows]


@router.get("/api/catalog")
def catalog(q: str = Query(default="", max_length=100), category: str | None = None,
            city: str | None = Query(default=None, max_length=80),
            price_min: int | None = Query(default=None, ge=0), price_max: int | None = Query(default=None, ge=0),
            sort: Literal["new", "price_asc", "price_desc"] = "new",
            housing_provided: bool | None = None, meals_provided: bool | None = None,
            condition: Literal["new", "used"] | None = None,
            rooms_min: int | None = Query(default=None, ge=0, le=20),
            limit: int = Query(default=20, ge=1, le=50), offset: int = Query(default=0, ge=0, le=10000),
            db: Session = Depends(get_db)):
    if category and category not in CATEGORIES:
        raise HTTPException(400, "Неизвестная категория")
    rows, total = search(db, q=q, category=category, city=city, price_min=price_min, price_max=price_max,
                         sort=sort, housing_provided=housing_provided, meals_provided=meals_provided,
                         condition=condition, rooms_min=rooms_min, limit=limit, offset=offset)
    return {"items": with_promo(db, rows), "total": total, "has_more": offset + len(rows) < total}


@router.get("/api/cities")
def cities(category: str | None = None, db: Session = Depends(get_db)):
    """Cities that have published listings, most popular first (for filter hints)."""
    stmt = select(Listing.city, func.count()).where(Listing.status == "approved")
    if category:
        stmt = stmt.where(Listing.category == category)
    rows = db.execute(stmt.group_by(Listing.city).order_by(func.count().desc(), Listing.city).limit(30)).all()
    return [{"city": city, "count": count} for city, count in rows]


@router.get("/api/listings/{listing_id}", response_model=ListingOut)
def listing_detail(listing_id: int, db: Session = Depends(get_db)):
    item = db.get(Listing, listing_id)
    if item is None or item.status != "approved":
        raise HTTPException(404, "Объявление не найдено или снято с публикации")
    return with_promo(db, [item])[0]


def backfill_search_fields(db: Session) -> None:
    """Fill the search columns for listings created before they existed."""
    rows = db.scalars(select(Listing).where(Listing.search_text.is_(None))).all()
    for item in rows:
        item.search_text = " ".join([item.title or "", item.description or "", item.city or ""]).lower()
        item.city_key = (item.city or "").strip().lower()
    if rows:
        db.commit()
