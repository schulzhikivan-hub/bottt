"""Browsing and search of published listings: words, filters, sorting, pages, details, cities."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, case, or_
from sqlalchemy.orm import Session

from app import fields, payments, photos
from app.payments import now
from app import guard
from app.database import get_db
from app.models import Listing, Placement
from app.schemas import CATEGORIES, ListingOut

router = APIRouter()

# Details belong to one section only: a job has visas, housing has a deposit, goods have a condition.
CATEGORY_EXTRAS = fields.CATEGORY_EXTRAS
ALL_EXTRAS = fields.ALL_EXTRAS
MAX_WORDS = 6


def clean_extras(category: str, data: dict) -> dict:
    """Values for a new listing: details of other sections are dropped, lists and details are normalised."""
    allowed = CATEGORY_EXTRAS.get(category, set())
    out = {key: (None if key in ALL_EXTRAS and key not in allowed else value) for key, value in data.items()}
    if "visas" in out:
        out["visas"] = fields.visas_to_text(out["visas"])
    if "details" in out:
        out["details"] = fields.clean_details(category, out["details"])
    return out


def drop_irrelevant(category: str, changes: dict) -> dict:
    """Changes to an existing listing: keep only what fits its section, normalise lists and details."""
    allowed = CATEGORY_EXTRAS.get(category, set())
    out = {key: value for key, value in changes.items() if key not in ALL_EXTRAS or key in allowed}
    if "visas" in out:
        out["visas"] = fields.visas_to_text(out["visas"])
    if "details" in out:
        out["details"] = fields.clean_details(category, out["details"])
    return out


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
           region: str | None = None, district_id: int | None = None,
           price_min: int | None = None, price_max: int | None = None, sort: str = "new",
           housing_provided: bool | None = None, meals_provided: bool | None = None,
           condition: str | None = None, rooms_min: int | None = None,
           visa: str | None = None, employment: str | None = None, schedule: str | None = None,
           housing_type: str | None = None, deposit_max: int | None = None, area_min: int | None = None,
           pets_allowed: bool | None = None, furnished: bool | None = None,
           goods_category: str | None = None,
           limit: int = 20, offset: int = 0) -> tuple[list[Listing], int]:
    """Published listings matching every condition. Paid placements come first on every page."""
    conditions = [Listing.status == "approved", or_(Listing.expires_at.is_(None), Listing.expires_at > now())]
    if category:
        conditions.append(Listing.category == category)
    if region:
        conditions.append(Listing.region_code == region)
    if district_id is not None:
        conditions.append(Listing.district_id == district_id)
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
    if visa:
        conditions.append(Listing.visas.like(f"%,{visa},%"))
    if employment:
        conditions.append(Listing.employment == employment)
    if schedule:
        conditions.append(Listing.schedule == schedule)
    if housing_type:
        conditions.append(Listing.housing_type == housing_type)
    if deposit_max is not None:
        conditions.append(Listing.deposit <= deposit_max)
    if area_min is not None:
        conditions.append(Listing.area_m2 >= area_min)
    if pets_allowed:
        conditions.append(Listing.pets_allowed.is_(True))
    if furnished:
        conditions.append(Listing.furnished.is_(True))
    if goods_category:
        conditions.append(Listing.goods_category == goods_category)

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


def with_promo(db: Session, rows: list[Listing], owner: bool = False) -> list[ListingOut]:
    """Output for the app. Public: only what is shown. Owner: everything, incl. waiting photos and notes."""
    ids = [x.id for x in rows]
    placed = payments.active_placements(db, ids)
    shots = photos.photos_map(db, ids, owner=owner)
    out = []
    for x in rows:
        pics = shots.get(x.id, [])
        update = {"promo": placed.get(x.id), "photos": pics,
                  "cover": next((p["thumb"] for p in pics if p["status"] != "pending"), None)}
        if not owner:
            update.update(note="", has_edit=False, expires_at=None)
        out.append(ListingOut.model_validate(x).model_copy(update=update))
    return out


@router.get("/api/catalog")
def catalog(q: str = Query(default="", max_length=100), category: str | None = None,
            city: str | None = Query(default=None, max_length=80),
            region: str | None = Query(default=None, max_length=20), district_id: int | None = None,
            price_min: int | None = Query(default=None, ge=0), price_max: int | None = Query(default=None, ge=0),
            sort: Literal["new", "price_asc", "price_desc"] = "new",
            housing_provided: bool | None = None, meals_provided: bool | None = None,
            condition: Literal["new", "used"] | None = None,
            rooms_min: int | None = Query(default=None, ge=0, le=20),
            visa: fields.VisaCode | None = None, employment: fields.Employment | None = None,
            schedule: fields.Schedule | None = None, housing_type: fields.HousingType | None = None,
            deposit_max: int | None = Query(default=None, ge=0), area_min: int | None = Query(default=None, ge=0),
            pets_allowed: bool | None = None, furnished: bool | None = None,
            goods_category: fields.GoodsCategory | None = None,
            limit: int = Query(default=20, ge=1, le=50), offset: int = Query(default=0, ge=0, le=10000),
            db: Session = Depends(get_db)):
    if category and category not in CATEGORIES:
        raise HTTPException(400, "Неизвестная категория")
    rows, total = search(db, q=q, category=category, city=city, region=region, district_id=district_id,
                         price_min=price_min, price_max=price_max,
                         sort=sort, housing_provided=housing_provided, meals_provided=meals_provided,
                         condition=condition, rooms_min=rooms_min, visa=visa, employment=employment,
                         schedule=schedule, housing_type=housing_type, deposit_max=deposit_max,
                         area_min=area_min, pets_allowed=pets_allowed, furnished=furnished,
                         goods_category=goods_category, limit=limit, offset=offset)
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
def listing_detail(listing_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.get(Listing, listing_id)
    if item is None or item.status != "approved":
        raise HTTPException(404, "Объявление не найдено или снято с публикации")
    if guard.first_view(request, listing_id):  # one view per visitor per hour, not per refresh
        item.views = (item.views or 0) + 1
        db.commit()
    return with_promo(db, [item])[0]


def backfill_search_fields(db: Session) -> None:
    """Fill the search columns for listings created before they existed."""
    rows = db.scalars(select(Listing).where(Listing.search_text.is_(None))).all()
    for item in rows:
        item.search_text = " ".join([item.title or "", item.description or "", item.city or ""]).lower()
        item.city_key = (item.city or "").strip().lower()
    if rows:
        db.commit()
