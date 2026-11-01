"""Browsing and search of published listings: words, filters, sorting, pages, details, cities."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, case, or_
from sqlalchemy.orm import Session

from app import applications, fields, payments, photos, policy, rules, searchdict, searchfix
from app.payments import now
from app import guard
from app.database import get_db
from app.models import District, Listing, Placement
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


def _penalty():
    """1 for a listing that several different people complained about (and nobody has decided yet): it sinks in its own group."""
    from sqlalchemy import String, cast, literal
    from app.models import Report
    n = rules.val("search.penalty_from")
    if not n:
        return literal(0)
    count = (select(func.count(func.distinct(Report.reporter_tg_id)))
             .where(Report.target_type == "listing", Report.target_id == cast(Listing.id, String), Report.status == "new")
             .correlate(Listing).scalar_subquery())
    return case((count >= n, 1), else_=0)


def _promo_rank(pin_only: bool = False):
    """Lift of paid placements in the list. A placement that a promotion gave free gets no lift (only a mark).
    For sorting by price only pinning counts: paid services must not break «cheapest first»."""
    if pin_only:
        rank = case((Placement.kind == "pin", 1), else_=0)
    else:
        rank = case((Placement.kind == "pin", 4), (Placement.kind == "vip", 3),
                    (Placement.kind == "bump", 2), (Placement.kind == "highlight", 1), else_=0)
    return (select(func.coalesce(func.max(rank), 0))
            .where(Placement.listing_id == Listing.id, Placement.status == "active", Placement.promo.is_(False))
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
           year_min: int | None = None, year_max: int | None = None, mileage_max: int | None = None,
           limit: int = 20, offset: int = 0, _fuzzy: bool = True, _info: dict | None = None) -> tuple[list[Listing], int]:
    """Published listings matching every condition. Paid placements come first on every page."""
    conditions = [Listing.status == "approved", Listing.quarantine.is_(False), or_(Listing.expires_at.is_(None), Listing.expires_at > now())]
    if category:
        conditions.append(Listing.category == category)
    if region:
        conditions.append(Listing.region_code == region)
    if district_id is not None:
        conditions.append(Listing.district_id == district_id)
    if city and city.strip():
        conditions.append(Listing.city_key.like(_like(city.strip().lower()), escape="\\"))
    # Every word must be found in the title, description or city (case is ignored); a word may also be read as
    # another keyboard layout, transliteration or with a different ending (app/searchfix.py)
    for word in searchdict.clean_words(q.lower().split())[:MAX_WORDS]:
        alts = set(searchfix.variants(word))
        for syn in searchdict.synonyms_of(word):                      # words the owner declared equal («квартира» = «студия»)
            alts |= set(searchfix.variants(syn))
        conditions.append(or_(*[Listing.search_text.like(_like(v), escape="\\") for v in alts]))
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
    if year_min is not None:
        conditions.append(Listing.year >= year_min)
    if year_max is not None:
        conditions.append(Listing.year <= year_max)
    if mileage_max is not None:
        conditions.append(Listing.mileage_km <= mileage_max)

    total = db.scalar(select(func.count()).select_from(Listing).where(*conditions)) or 0
    if total == 0 and q.strip() and _fuzzy:
        # nothing found: try to fix typos using the words that really occur in published listings
        vocab = searchfix.vocabulary(db)
        fixed = [searchfix.correct(w, vocab) or w for w in q.lower().split()[:MAX_WORDS]]
        if fixed != q.lower().split()[:MAX_WORDS]:
            if _info is not None:
                _info["corrected"] = " ".join(fixed)
            return search(db, q=" ".join(fixed), category=category, city=city, region=region, district_id=district_id,
                          price_min=price_min, price_max=price_max, sort=sort, housing_provided=housing_provided,
                          meals_provided=meals_provided, condition=condition, rooms_min=rooms_min, visa=visa,
                          employment=employment, schedule=schedule, housing_type=housing_type, deposit_max=deposit_max,
                          area_min=area_min, pets_allowed=pets_allowed, furnished=furnished, goods_category=goods_category,
                          year_min=year_min, year_max=year_max, mileage_max=mileage_max, limit=limit, offset=offset, _fuzzy=False)
    if sort == "price_asc":
        order = [Listing.price_amount.asc().nulls_last(), Listing.id.desc()]
    elif sort == "price_desc":
        order = [Listing.price_amount.desc().nulls_last(), Listing.id.desc()]
    else:
        order = [Listing.id.desc()]
    rows = db.scalars(select(Listing).where(*conditions)
                      .order_by(_promo_rank(pin_only=sort in {"price_asc", "price_desc"}).desc(), _penalty().asc(), *order).limit(limit).offset(offset)).all()
    return list(rows), total


def with_promo(db: Session, rows: list[Listing], owner: bool = False) -> list[ListingOut]:
    """Output for the app. Public: only what is shown. Owner: everything, incl. waiting photos and notes."""
    ids = [x.id for x in rows]
    info = payments.active_placement_info(db, ids)
    placed = {k: v[0] for k, v in info.items()}
    shots = photos.photos_map(db, ids, owner=owner)
    applied = applications.counts(db, ids) if owner else {}
    running: dict[int, list[dict]] = {}
    if owner and ids:
        for pl in db.scalars(select(Placement).where(Placement.listing_id.in_(ids), Placement.status == "active")).all():
            if payments.aware(pl.ends_at) > payments.now():
                running.setdefault(pl.listing_id, []).append({"kind": pl.kind, "ends_at": str(pl.ends_at)})
    from app.company import Company
    cids = {x.company_id for x in rows if x.company_id}
    cos = {c.id: c for c in db.scalars(select(Company).where(Company.id.in_(cids))).all()} if cids else {}
    out = []
    for x in rows:
        pics = shots.get(x.id, [])
        co = cos.get(x.company_id)
        update = {"company_name": co.name if co else "", "company_verified": bool(co and co.status == "verified"),
                  "promo": placed.get(x.id), "promo_free": bool(info.get(x.id, ("", False))[1]), "photos": pics,
                  "cover": next((p["thumb"] for p in pics if p["status"] != "pending"), None)}
        if not owner:
            update.update(note="", has_edit=False, expires_at=None)
        else:
            update["placements"] = running.get(x.id, [])
            if x.category == "job":
                update["applications"] = applied.get(x.id, {"total": 0, "new": 0})
        out.append(ListingOut.model_validate(x).model_copy(update=update))
    return out


RELAX_LABELS = {"city": "Искать по всей стране", "district_id": "Без района", "region": "Во всех регионах", "price_min": "Без нижней цены",
                "price_max": "Без верхней цены", "q": "Без слов поиска", "visa": "Без визы", "employment": "Любая занятость",
                "schedule": "Любой график", "housing_type": "Любой тип жилья", "deposit_max": "Без ограничения залога",
                "area_min": "Любая площадь", "rooms_min": "Любое число комнат", "year_min": "Любой год", "year_max": "Любой год",
                "mileage_max": "Любой пробег", "condition": "Новые и б/у", "goods_category": "Любая категория вещей",
                "housing_provided": "С жильём и без", "meals_provided": "С питанием и без", "pets_allowed": "С животными и без",
                "furnished": "С мебелью и без", "category": "Во всех разделах"}


def _relax(db: Session, args: dict, sort: str) -> list[dict]:
    """The result is empty: which single filter, if removed, brings results? At most three suggestions, the richest first."""
    given = {k: v for k, v in args.items() if v not in (None, "", False)}
    if not given:
        return []
    found = []
    for key in given:
        trial = {k: v for k, v in args.items() if k != key}
        _, count = search(db, sort=sort, limit=1, offset=0, **trial)
        if count:
            found.append({"drop": key, "label": RELAX_LABELS.get(key, "Без этого условия"), "total": count})
    found.sort(key=lambda r: -r["total"])
    labels, out = set(), []
    for row in found:                                              # «Любой год» twice is noise
        if row["label"] not in labels:
            labels.add(row["label"])
            out.append(row)
    return out[:3]


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
            year_min: int | None = Query(default=None, ge=1950, le=2100), year_max: int | None = Query(default=None, ge=1950, le=2100),
            mileage_max: int | None = Query(default=None, ge=0, le=3_000_000),
            limit: int = Query(default=20, ge=1, le=50), offset: int = Query(default=0, ge=0, le=10000),
            db: Session = Depends(get_db)):
    if category and category not in CATEGORIES:
        raise HTTPException(400, "Неизвестная категория")
    info: dict = {}
    rows, total = search(db, q=q, category=category, city=city, region=region, district_id=district_id,
                         price_min=price_min, price_max=price_max, _info=info,
                         sort=sort, housing_provided=housing_provided, meals_provided=meals_provided,
                         condition=condition, rooms_min=rooms_min, visa=visa, employment=employment,
                         schedule=schedule, housing_type=housing_type, deposit_max=deposit_max,
                         area_min=area_min, pets_allowed=pets_allowed, furnished=furnished,
                         goods_category=goods_category, year_min=year_min, year_max=year_max,
                         mileage_max=mileage_max, limit=limit, offset=offset)
    items = with_promo(db, rows)
    if sort == "new":
        # «2 из 5», 30% страницы, не больше двух закреплённых: одни правила для всех списков (app/policy.py)
        items = policy.arrange(items, lambda o: "promo" if o.promo_free else (o.promo if o.promo in {"pin", "vip", "bump"} else ""))
    out = {"items": items, "total": total, "has_more": offset + len(rows) < total}
    if q.strip() and offset == 0:
        from app import searchlog
        searchlog.record(db, q, total)                           # the cleaned text and the count only, no person
    if total == 0 and offset == 0:
        out["relax"] = _relax(db, dict(
            q=q, category=category, city=city, region=region, district_id=district_id, price_min=price_min, price_max=price_max,
            housing_provided=housing_provided, meals_provided=meals_provided, condition=condition, rooms_min=rooms_min, visa=visa,
            employment=employment, schedule=schedule, housing_type=housing_type, deposit_max=deposit_max, area_min=area_min,
            pets_allowed=pets_allowed, furnished=furnished, goods_category=goods_category, year_min=year_min, year_max=year_max,
            mileage_max=mileage_max), sort)
    if info.get("corrected"):
        out["corrected"] = info["corrected"]      # the app says «показано по запросу …»
    return out


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
    out = with_promo(db, [item])[0]
    return out.model_copy(update={"author": applications.author_card(db, item)})


@router.get("/api/listings/{listing_id}/similar")
def similar(listing_id: int, db: Session = Depends(get_db)):
    """Up to 6 other published listings of the same category: same city first, then the same region."""
    item = db.get(Listing, listing_id)
    if item is None or item.status != "approved":
        raise HTTPException(404, "Объявление не найдено или снято с публикации")
    live = [Listing.status == "approved", Listing.id != item.id, Listing.category == item.category,
            Listing.author_telegram_id != item.author_telegram_id,
            or_(Listing.expires_at.is_(None), Listing.expires_at > now())]
    same_city = db.scalars(select(Listing).where(*live, Listing.city_key == item.city_key)
                           .order_by(Listing.id.desc()).limit(6)).all()
    rows = list(same_city)
    if len(rows) < 6 and item.region_code:
        taken = [r.id for r in rows]
        more = db.scalars(select(Listing).where(*live, Listing.region_code == item.region_code, Listing.id.not_in(taken or [0]))
                          .order_by(Listing.id.desc()).limit(6 - len(rows))).all()
        rows += list(more)
    return with_promo(db, rows)


@router.get("/api/authors/{listing_id}")
def author_page(listing_id: int, db: Session = Depends(get_db)):
    """Everything one author offers right now. Found through one of their listings, so no Telegram ID is exposed."""
    item = db.get(Listing, listing_id)
    if item is None or item.status != "approved":
        raise HTTPException(404, "Объявление не найдено или снято с публикации")
    who = (Listing.company_id == item.company_id) if item.company_id else \
        ((Listing.author_telegram_id == item.author_telegram_id) & Listing.company_id.is_(None))
    rows = db.scalars(select(Listing).where(
        who, Listing.status == "approved",
        or_(Listing.expires_at.is_(None), Listing.expires_at > now())).order_by(Listing.id.desc()).limit(30)).all()
    return {"card": applications.author_card(db, item), "items": with_promo(db, list(rows))}


@router.get("/api/suggest")
def suggest(q: str = Query(min_length=1, max_length=40), db: Session = Depends(get_db)):
    """Hints while typing: titles of published listings and Korean places that contain the word."""
    word = q.strip().lower()
    if not word:
        return []
    titles = db.execute(select(Listing.title).where(
        Listing.status == "approved", Listing.search_text.like(_like(word), escape="\\"))
        .group_by(Listing.title).order_by(func.count().desc(), Listing.title).limit(5)).scalars().all()
    places = [d.label for d in db.scalars(select(District).order_by(District.sort)).all()
              if word in d.name_ru.lower() or word in d.label.lower()][:4]
    return ([{"type": "title", "text": t} for t in titles] + [{"type": "place", "text": p} for p in places])[:8]


def backfill_search_fields(db: Session) -> None:
    """Fill the search columns for listings created before they existed."""
    rows = db.scalars(select(Listing).where(Listing.search_text.is_(None))).all()
    for item in rows:
        item.search_text = " ".join([item.title or "", item.description or "", item.city or ""]).lower()
        item.city_key = (item.city or "").strip().lower()
    if rows:
        db.commit()
