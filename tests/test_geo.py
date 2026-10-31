import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func

from app import geo
from app.database import SessionLocal, engine
from app.main import app
from app.models import District, Region, Listing
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    reset_db()
    with TestClient(app) as c:
        accept_terms(c, 1)
        yield c


def regions(client):
    return {r["code"]: r for r in client.get("/api/geo").json()["regions"]}


def make(client, **fields):
    body = {"category": "marketplace", "title": "Стол", "price": "", "description": "ok", "contact": ""}
    body.update(fields)
    return client.post("/api/listings", json=body, headers=as_user(1))


# ---- the reference list ----

def test_seventeen_regions_and_all_229_places(client):
    r = regions(client)
    assert len(r) == 17
    assert sum(len(x["districts"]) for x in r.values()) == 229


def test_known_places_are_there_with_korean_names(client):
    r = regions(client)
    assert len(r["seoul"]["districts"]) == 25
    seoul = {d["name"]: d["name_ko"] for d in r["seoul"]["districts"]}
    assert seoul["Каннам"] == "강남구"
    assert {d["name"] for d in r["chungnam"]["districts"]} >= {"Асан", "Чхонан", "Танджин"}
    assert {d["name"] for d in r["gyeonggi"]["districts"]} >= {"Сувон", "Ансан", "Хвасон", "Сихын"}
    assert {d["name"] for d in r["jeju"]["districts"]} == {"Чеджу", "Согвипхо"}
    assert r["gwangju"]["name_ko"] == "광주광역시"


def test_region_counts_match_official_structure(client):
    r = regions(client)
    expected = {"seoul": 25, "busan": 16, "daegu": 9, "incheon": 10, "gwangju": 5, "daejeon": 5, "ulsan": 5,
                "sejong": 1, "gyeonggi": 31, "gangwon": 18, "chungbuk": 11, "chungnam": 15, "jeonbuk": 14,
                "jeonnam": 22, "gyeongbuk": 22, "gyeongnam": 18, "jeju": 2}
    assert {code: len(x["districts"]) for code, x in r.items()} == expected


def test_labels_are_unique_so_nothing_is_ambiguous(client):
    labels = [d["label"] for x in regions(client).values() for d in x["districts"]]
    assert len(labels) == len(set(labels))
    assert "Сеул, Каннам" in labels and "Асан" in labels
    # Names that exist twice get their region: Kosong (two), Kochkhan (two), Gyeonggi's Gwangju
    assert "Косон (Канвондо)" in labels and "Косон (Кёнсан-Намдо)" in labels
    assert "Кванджу (Кёнгидо)" in labels
    assert "Седжон" in labels and "Чеджу" in labels  # a city named like its region keeps a plain label


def test_geo_endpoint_is_cacheable(client):
    assert "max-age" in client.get("/api/geo").headers["cache-control"]


def test_no_empty_or_repeated_korean_names_inside_a_region(client):
    with SessionLocal() as db:
        rows = db.execute(select(District.region_code, District.name_ko, func.count()).group_by(
            District.region_code, District.name_ko)).all()
        assert all(n == 1 and ko for _, ko, n in rows)
        assert db.scalar(select(func.count()).select_from(Region)) == 17


# ---- choosing a place ----

def test_listing_by_district_gets_label_region_and_district(client):
    asan = next(d for d in regions(client)["chungnam"]["districts"] if d["name"] == "Асан")
    r = make(client, district_id=asan["id"])
    assert r.status_code == 201 and r.json()["city"] == "Асан"
    with SessionLocal() as db:
        item = db.get(Listing, r.json()["id"])
        assert item.region_code == "chungnam" and item.district_id == asan["id"]


def test_metropolitan_district_label_contains_the_city(client):
    kn = next(d for d in regions(client)["seoul"]["districts"] if d["name"] == "Каннам")
    assert make(client, district_id=kn["id"]).json()["city"] == "Сеул, Каннам"


def test_region_alone_is_allowed_by_api(client):
    r = make(client, region_code="busan")
    assert r.status_code == 201 and r.json()["city"] == "Пусан"


def test_free_text_must_name_exactly_one_place(client):
    assert make(client, city="Асан").status_code == 201
    assert make(client, city="아산시").status_code == 201          # Korean works too
    assert make(client, city="сеул, каннам").status_code == 201  # any case
    assert make(client, city="Москва").status_code == 400
    assert make(client, city="где-то рядом").status_code == 400
    assert make(client, city="Чун").status_code == 400            # exists in several cities: ambiguous
    assert make(client).status_code == 400                         # no place at all


def test_unknown_district_or_region_is_refused(client):
    assert make(client, district_id=999999).status_code == 400
    assert make(client, region_code="moscow").status_code == 400


def test_region_must_match_the_district(client):
    asan = next(d for d in regions(client)["chungnam"]["districts"] if d["name"] == "Асан")
    assert make(client, district_id=asan["id"], region_code="seoul").status_code == 400
    assert make(client, district_id=asan["id"], region_code="chungnam").status_code == 201


def test_editing_the_place_is_validated(client):
    lid = make(client, city="Асан").json()["id"]
    ok = client.put(f"/api/listings/{lid}", json={"region_code": "jeju"}, headers=as_user(1))
    assert ok.status_code == 200 and ok.json()["city"] == "Чеджу"
    bad = client.put(f"/api/listings/{lid}", json={"city": "Токио"}, headers=as_user(1))
    assert bad.status_code == 400


def test_profile_city_must_be_in_korea(client):
    assert client.put("/api/me/settings", json={"city": "Пусан"}, headers=as_user(1)).json()["city"] == "Пусан"
    assert client.put("/api/me/settings", json={"city": "Бишкек"}, headers=as_user(1)).status_code == 400
    assert client.put("/api/me/settings", json={}, headers=as_user(1)).status_code == 400


def test_dating_profile_city_must_be_in_korea(client):
    body = {"birth_year": 1995, "gender": "f", "bio": ""}
    assert client.put("/api/dating/profile", json={**body, "city": "Инчхон"}, headers=as_user(1)).status_code == 200
    assert client.put("/api/dating/profile", json={**body, "city": "Лондон"}, headers=as_user(1)).status_code == 400
    assert client.put("/api/dating/profile", json=body, headers=as_user(1)).status_code == 400


# ---- filters by place ----

def test_filter_by_region_and_by_district(client):
    asan = next(d for d in regions(client)["chungnam"]["districts"] if d["name"] == "Асан")
    kn = next(d for d in regions(client)["seoul"]["districts"] if d["name"] == "Каннам")
    a = make(client, district_id=asan["id"]).json()["id"]
    b = make(client, district_id=kn["id"]).json()["id"]
    c = make(client, region_code="seoul").json()["id"]
    for lid in (a, b, c):
        client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    ids = lambda **p: sorted(x["id"] for x in client.get("/api/catalog", params=p).json()["items"])
    assert ids(region="seoul") == sorted([b, c])
    assert ids(district_id=asan["id"]) == [a]
    assert ids(region="chungnam", district_id=asan["id"]) == [a]
    assert ids(region="jeju") == []


# ---- old data ----

def test_old_listings_with_free_text_get_a_place_when_possible(client):
    with SessionLocal() as db:
        db.add(Listing(category="job", title="Старое", city="Ансан", author_telegram_id="9", status="approved"))
        db.add(Listing(category="job", title="Странное", city="Нарния", author_telegram_id="9", status="approved"))
        db.commit()
        geo.backfill_places(db)
        old = db.scalar(select(Listing).where(Listing.title == "Старое"))
        odd = db.scalar(select(Listing).where(Listing.title == "Странное"))
        assert old.region_code == "gyeonggi" and old.district_id
        assert odd.region_code is None and odd.city == "Нарния"  # left alone, not guessed
