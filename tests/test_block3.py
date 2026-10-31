import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal, engine
from app.main import app
from app.models import Article, Group, Listing
from app.payments import now
from tests.tg import accept_terms, as_user, reset_db

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def make(client, user=1, publish=True, **kw):
    body = {"category": "marketplace", "title": "Объявление", "city": "Сеул", "description": "ok"}
    body.update(kw)
    r = client.post("/api/listings", json=body, headers=as_user(user))
    assert r.status_code == 201, r.text
    lid = r.json()["id"]
    if publish:
        client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def ids(client, **params):
    return [x["id"] for x in client.get("/api/catalog", params=params).json()["items"]]


def test_car_filters_year_and_mileage(client):
    old = make(client, title="Старая", goods_category="auto", details={"year": 2008, "mileage_km": 240000})
    mid = make(client, title="Средняя", goods_category="auto", details={"year": 2015, "mileage_km": 120000})
    new = make(client, title="Новая", goods_category="auto", details={"year": 2022, "mileage_km": 15000})
    make(client, title="Диван", goods_category="furniture")
    assert set(ids(client, goods_category="auto", year_min=2015)) == {mid, new}
    assert ids(client, goods_category="auto", year_max=2010) == [old]
    assert set(ids(client, goods_category="auto", mileage_max=130000)) == {mid, new}
    assert ids(client, goods_category="auto", year_min=2015, mileage_max=50000) == [new]
    assert client.get("/api/catalog", params={"year_min": 1800}).status_code == 422


def test_year_follows_an_edit_of_the_details(client):
    lid = make(client, title="Машина", goods_category="auto", details={"year": 2010})
    client.put(f"/api/listings/{lid}", json={"details": {"year": 2020}}, headers=as_user(1))
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)   # the edit waits for a moderator
    assert ids(client, year_min=2019) == [lid]
    assert ids(client, year_max=2012) == []


def test_saved_search_accepts_car_filters(client):
    r = client.post("/api/me/searches", json={"category": "marketplace", "goods_category": "auto", "year_min": 2015, "mileage_max": 90000},
                    headers=as_user(2))
    assert r.status_code == 201
    assert client.get("/api/me/searches", headers=as_user(2)).json()[0]["params"]["year_min"] == 2015


def test_reserved_mark(client):
    lid = make(client)
    job = make(client, category="job")
    r = client.post(f"/api/listings/{lid}/reserve", json={"on": True}, headers=as_user(1))
    assert r.status_code == 200 and r.json()["reserved"] is True
    assert [x for x in client.get("/api/catalog").json()["items"] if x["id"] == lid][0]["reserved"] is True
    assert client.post(f"/api/listings/{lid}/reserve", json={"on": True}, headers=as_user(2)).status_code == 404   # not yours
    assert client.post(f"/api/listings/{job}/reserve", json={"on": True}, headers=as_user(1)).status_code == 400   # vacancies cannot
    assert client.post(f"/api/listings/{lid}/reserve", json={"on": False}, headers=as_user(1)).json()["reserved"] is False
    draft = make(client, publish=False)
    assert client.post(f"/api/listings/{draft}/reserve", json={"on": True}, headers=as_user(1)).status_code == 409


def test_author_page_shows_only_their_published_listings_without_id(client):
    a = make(client, title="Первое")
    b = make(client, title="Второе")
    make(client, title="Чужое", user=2)
    make(client, title="Черновик", publish=False)
    data = client.get(f"/api/authors/{a}").json()
    assert {x["title"] for x in data["items"]} == {"Первое", "Второе"}
    assert "author_telegram_id" not in str(data) and "1" not in [str(v) for v in data["card"].values() if v == "1"]
    assert set(data["card"]) >= {"verified", "company", "active", "since"}
    assert client.get("/api/authors/99999").status_code == 404
    client.post(f"/api/listings/{b}/close", headers=as_user(1))
    assert {x["title"] for x in client.get(f"/api/authors/{a}").json()["items"]} == {"Первое"}


def test_suggest_titles_and_places(client):
    make(client, title="Диван угловой")
    make(client, title="Диван детский")
    s = client.get("/api/suggest", params={"q": "диван"}).json()
    assert [x["text"] for x in s if x["type"] == "title"] == ["Диван детский", "Диван угловой"]
    p = client.get("/api/suggest", params={"q": "каннам"}).json()
    assert any(x["type"] == "place" and "Каннам" in x["text"] for x in p)
    assert client.get("/api/suggest", params={"q": "%"}).json() == []
    assert client.get("/api/suggest", params={"q": ""}).status_code == 422


def test_groups_have_kind_and_members(client):
    with SessionLocal() as db:
        g = db.scalar(select(Group))
        gid = g.id
    r = client.put(f"/api/admin/groups/{gid}?members_count=1200&kind=city&telegram_link=https://t.me/x_group", headers=ADMIN)
    assert r.status_code == 200
    g = [x for x in client.get("/api/groups").json() if x["id"] == gid][0]
    assert g["kind"] == "city" and g["members_count"] == 1200
    assert client.put(f"/api/admin/groups/{gid}?kind=zzz", headers=ADMIN).status_code == 400
    assert all(x["kind"] in ("nation", "city") for x in client.get("/api/groups").json())


def test_guide_bookmarks_and_search(client):
    with SessionLocal() as db:
        db.add(Article(section="visas", title="Продление E-9", body="Нужен паспорт и контракт", published=True, checked_at=now()))
        db.add(Article(section="banks", title="Карта", body="Банковский счёт", published=True, checked_at=now()))
        db.commit()
        aid = db.scalar(select(Article.id).where(Article.title == "Продление E-9"))
    assert len(client.get("/api/articles").json()) == 2
    assert [a["title"] for a in client.get("/api/articles", params={"q": "паспорт"}).json()] == ["Продление E-9"]
    assert client.get("/api/articles", params={"q": "нет такого"}).json() == []
    assert client.post(f"/api/articles/{aid}/bookmark", headers=as_user(2)).json()["saved"] is True
    assert [a["title"] for a in client.get("/api/me/bookmarks", headers=as_user(2)).json()] == ["Продление E-9"]
    assert client.get("/api/me/bookmarks", headers=as_user(3)).json() == []
    saved = [a for a in client.get("/api/articles", headers=as_user(2)).json() if a["id"] == aid][0]
    assert saved["saved"] is True
    assert client.post(f"/api/articles/{aid}/bookmark", headers=as_user(2)).json()["saved"] is False
    assert client.post("/api/articles/9999/bookmark", headers=as_user(2)).status_code == 404
