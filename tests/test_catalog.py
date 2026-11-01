import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import payments
from app.database import SessionLocal, engine
from app.main import app
from app.models import Placement, Report
from tests.tg import as_user, reset_db, accept_terms
from sqlalchemy import select

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def make(client, user=1, publish=True, **fields):
    body = {"category": "marketplace", "title": "Стол", "city": "Сеул", "price": "", "description": "ok", "contact": ""}
    body.update(fields)
    r = client.post("/api/listings", json=body, headers=as_user(user))
    assert r.status_code == 201, r.text
    lid = r.json()["id"]
    if publish:
        client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def ids(client, **params):
    return [x["id"] for x in client.get("/api/catalog", params=params).json()["items"]]


# ---- words ----

def test_search_ignores_case_in_russian(client):
    sofa = make(client, title="Диван угловой", description="Самовывоз из Инчхона")
    make(client, title="Стол")
    assert ids(client, q="диван") == [sofa]
    assert ids(client, q="ДИВАН") == [sofa]
    assert ids(client, q="инчхона") == [sofa]  # found by the description


def test_every_word_must_match(client):
    a = make(client, title="Велосипед горный", city="Пусан")
    make(client, title="Велосипед детский", city="Сеул")
    assert ids(client, q="велосипед пусан") == [a]
    assert ids(client, q="велосипед") != [a]


def test_percent_and_underscore_are_plain_characters(client):
    make(client, title="Скидка 100% на всё")
    make(client, title="Обычный товар")
    assert len(ids(client, q="100%")) == 1
    assert ids(client, q="%") != ids(client)  # not a wildcard that matches everything
    assert len(ids(client, q="_")) == 0


def test_unpublished_listings_are_never_found(client):
    make(client, title="Тайный товар", publish=False)
    assert ids(client, q="тайный") == []


# ---- filters and sorting ----

def test_category_and_city_filters(client):
    job = make(client, category="job", city="Асан")
    flat = make(client, category="housing", city="Сеул, Каннам")
    assert ids(client, category="job") == [job]
    assert ids(client, city="СЕУЛ") == [flat]  # part of the city, any case
    assert client.get("/api/catalog", params={"category": "cars"}).status_code == 400


def test_price_range_and_sorting(client):
    cheap = make(client, title="Дешёвый", price_amount=10_000)
    mid = make(client, title="Средний", price_amount=50_000)
    rich = make(client, title="Дорогой", price_amount=200_000)
    unknown = make(client, title="Без цены")
    assert ids(client, price_min=20_000, price_max=100_000) == [mid]
    assert ids(client, sort="price_asc") == [cheap, mid, rich, unknown]  # no price goes last
    assert ids(client, sort="price_desc") == [rich, mid, cheap, unknown]
    assert ids(client, sort="new")[0] == unknown


def test_vacancy_filters_for_housing_and_meals(client):
    both = make(client, category="job", housing_provided=True, meals_provided=True)
    housing_only = make(client, category="job", housing_provided=True)
    make(client, category="job")
    assert set(ids(client, category="job", housing_provided="true")) == {both, housing_only}
    assert ids(client, category="job", housing_provided="true", meals_provided="true") == [both]


def test_rooms_and_condition_filters(client):
    big = make(client, category="housing", rooms=3)
    make(client, category="housing", rooms=1)
    new = make(client, condition="new")
    make(client, condition="used")
    assert ids(client, category="housing", rooms_min=2) == [big]
    assert ids(client, condition="new") == [new]


def test_details_that_do_not_fit_the_section_are_dropped(client):
    job = make(client, category="job", rooms=4, condition="new", housing_provided=True)
    flat = make(client, category="housing", housing_provided=True, rooms=2)
    jobs = {x["id"]: x for x in client.get("/api/catalog").json()["items"]}
    assert jobs[job]["rooms"] is None and jobs[job]["condition"] is None
    assert jobs[job]["housing_provided"] is True
    assert jobs[flat]["housing_provided"] is None and jobs[flat]["rooms"] == 2


def test_invalid_details_are_refused(client):
    bad = {"category": "marketplace", "title": "Стол", "city": "Сеул", "condition": "broken"}
    assert client.post("/api/listings", json=bad, headers=as_user(1)).status_code == 422
    bad = {"category": "marketplace", "title": "Стол", "city": "Сеул", "price_amount": -5}
    assert client.post("/api/listings", json=bad, headers=as_user(1)).status_code == 422


# ---- pages ----

def test_pages_total_and_has_more(client):
    for i in range(7):
        make(client, user=1 + i % 3, category=["job", "housing", "marketplace"][i % 3], title=f"Товар {i}")
    first = client.get("/api/catalog", params={"limit": 3}).json()
    assert first["total"] == 7 and len(first["items"]) == 3 and first["has_more"] is True
    last = client.get("/api/catalog", params={"limit": 3, "offset": 6}).json()
    assert len(last["items"]) == 1 and last["has_more"] is False
    everything = [x["id"] for p in (0, 3, 6) for x in client.get("/api/catalog", params={"limit": 3, "offset": p}).json()["items"]]
    assert len(everything) == len(set(everything)) == 7  # no repeats, nothing lost


def test_page_size_is_limited(client):
    assert client.get("/api/catalog", params={"limit": 500}).status_code == 422


def test_paid_listing_comes_first_on_the_first_page(client):
    old = make(client, title="Старое оплаченное")
    for i in range(4):
        make(client, title=f"Новое {i}")
    with SessionLocal() as db:
        db.add(Placement(listing_id=old, kind="vip", starts_at=payments.now(),
                         ends_at=payments.now() + dt.timedelta(days=7), status="active"))
        db.commit()
    page = client.get("/api/catalog", params={"limit": 2}).json()["items"]
    assert page[0]["id"] == old and page[0]["promo"] == "vip"
    assert page[1]["promo"] is None


def test_ended_placement_stops_promoting(client):
    old = make(client, title="Старое")
    make(client, title="Новое")
    with SessionLocal() as db:
        db.add(Placement(listing_id=old, kind="pin", starts_at=payments.now(),
                         ends_at=payments.now(), status="ended"))
        db.commit()
    assert ids(client)[0] != old


# ---- detail and cities ----

def test_detail_of_published_listing(client):
    lid = make(client, category="job", title="Работа", housing_provided=True, price_amount=2_800_000)
    detail = client.get(f"/api/listings/{lid}").json()
    assert detail["title"] == "Работа" and detail["housing_provided"] is True and detail["price_amount"] == 2_800_000
    assert detail["created_at"]


def test_detail_hides_unpublished_listing(client):
    lid = make(client, publish=False)
    assert client.get(f"/api/listings/{lid}").status_code == 404
    assert client.get("/api/listings/99999").status_code == 404


def test_cities_with_counts(client):
    make(client, city="Сеул")
    make(client, city="Сеул")
    make(client, city="Пусан")
    make(client, city="Седжон", publish=False)
    cities = client.get("/api/cities").json()
    assert cities[0] == {"city": "Сеул", "count": 2}
    assert {"city": "Пусан", "count": 1} in cities
    assert all(c["city"] != "Седжон" for c in cities)


# ---- editing keeps search working ----

def test_edit_waits_for_moderation_while_the_old_version_stays_visible(client):
    lid = make(client, title="Старое название")
    r = client.put(f"/api/listings/{lid}", json={"title": "Новое название", "price_amount": 70_000}, headers=as_user(1))
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["has_edit"] is True
    assert ids(client, q="старое") == [lid] and ids(client, q="новое") == []  # nothing changed for visitors
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    assert ids(client, q="новое") == [lid]
    assert ids(client, q="старое") == []
    assert ids(client, price_max=80_000) == [lid]


def test_moderator_comment_reaches_the_author(client):
    lid = make(client, publish=False)
    client.post(f"/api/admin/listings/{lid}/needs_fix?reason=Добавьте+график", headers=ADMIN)
    mine = client.get("/api/me/listings", headers=as_user(1)).json()
    assert mine[0]["status"] == "needs_fix" and mine[0]["note"] == "Добавьте график"


# ---- chat tools ----

def start_chat(client):
    lid = make(client, user=1)
    return client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]


def test_block_from_chat_does_not_reveal_the_other_id(client):
    conv = start_chat(client)
    r = client.post(f"/api/conversations/{conv}/block", headers=as_user(2))
    assert r.status_code == 200 and "1" not in str(r.json().get("blocked_id", ""))
    assert client.post(f"/api/conversations/{conv}/messages", json={"text": "привет"}, headers=as_user(1)).status_code == 403
    assert client.get("/api/conversations", headers=as_user(2)).json() == []


def test_report_from_chat_goes_to_moderators(client):
    conv = start_chat(client)
    r = client.post(f"/api/conversations/{conv}/report", json={"reason": "spam"}, headers=as_user(2))
    assert r.status_code == 201
    with SessionLocal() as db:
        report = db.scalar(select(Report))
        assert report.target_type == "user" and report.target_id == "1" and report.reporter_tg_id == "2"


def test_stranger_cannot_use_chat_tools(client):
    conv = start_chat(client)
    assert client.post(f"/api/conversations/{conv}/block", headers=as_user(3)).status_code == 404
    assert client.post(f"/api/conversations/{conv}/report", json={"reason": "spam"}, headers=as_user(3)).status_code == 404


def test_empty_result_suggests_which_filter_to_drop(client):
    make(client, title="Стол", price="50000")
    r = client.get("/api/catalog", params={"q": "стол", "price_max": 1000}).json()
    assert r["total"] == 0
    assert any(x["drop"] == "price_max" and x["total"] == 1 for x in r["relax"])
    assert "relax" not in client.get("/api/catalog", params={"q": "стол"}).json()      # only when empty
