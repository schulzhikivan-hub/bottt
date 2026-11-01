import pytest
from fastapi.testclient import TestClient

from app import viewstats
from app.database import SessionLocal, engine
from app.main import app
from app.models import Listing
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def make(client, user=1, **fields):
    body = {"category": "marketplace", "title": "Велосипед", "city": "Сеул", "description": "ok", "contact": "", "price_amount": 100000} | fields
    lid = client.post("/api/listings", json=body, headers=as_user(user)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def views(client, lid):
    with SessionLocal() as db:
        return db.get(Listing, lid).views


def test_author_staff_and_robots_do_not_count_but_a_person_counts_once_a_day(client):
    lid = make(client)
    for _ in range(3):
        client.get(f"/api/listings/{lid}", headers=as_user(1))                        # the author
    client.get(f"/api/listings/{lid}", headers=as_user(1) | ADMIN)
    client.get(f"/api/listings/{lid}", headers={"user-agent": "Googlebot/2.1"})
    assert views(client, lid) == 0
    for _ in range(3):
        client.get(f"/api/listings/{lid}", headers=as_user(2))
    assert views(client, lid) == 1                                                    # one person, one day
    client.get(f"/api/listings/{lid}", headers=as_user(3))
    assert views(client, lid) == 2
    with SessionLocal() as db:
        today = viewstats.daily(db, lid, 3)[-1]
    assert today["views"] == 2


def test_stats_have_a_daily_series(client):
    lid = make(client)
    client.get(f"/api/listings/{lid}", headers=as_user(2))
    st = client.get(f"/api/listings/{lid}/stats", headers=as_user(1)).json()
    assert len(st["daily"]) == 14 and st["daily"][-1]["views"] == 1


def test_quick_price_change_rules_and_history(client):
    lid = make(client)
    assert client.post(f"/api/listings/{lid}/price", json={"price_amount": 90000}, headers=as_user(1)).json()["price_amount"] == 90000
    assert client.post(f"/api/listings/{lid}/price", json={"price_amount": 90000}, headers=as_user(1)).status_code == 409        # same
    assert client.post(f"/api/listings/{lid}/price", json={"price_amount": 200000}, headers=as_user(1)).status_code == 409       # over 50%
    assert client.post(f"/api/listings/{lid}/price", json={"price_amount": 80000}, headers=as_user(2)).status_code == 404        # not yours
    client.post(f"/api/listings/{lid}/price", json={"price_amount": 85000}, headers=as_user(1))
    client.post(f"/api/listings/{lid}/price", json={"price_amount": 80000}, headers=as_user(1))
    assert client.post(f"/api/listings/{lid}/price", json={"price_amount": 75000}, headers=as_user(1)).status_code == 429       # 3 a day
    h = client.get(f"/api/listings/{lid}/history", headers=as_user(1)).json()
    assert [e["what"] for e in h].count("Изменена цена") == 3 and h[-1]["what"] == "Объявление создано"
    assert client.get(f"/api/listings/{lid}/history", headers=as_user(2)).status_code == 404


def test_unread_badge_counts_only_unread_messages_of_visible_chats(client):
    lid = make(client)
    cid = client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": "Здравствуйте"}, headers=as_user(2))
    client.post(f"/api/conversations/{cid}/messages", json={"text": "Ещё вопрос"}, headers=as_user(2))
    assert client.get("/api/me/unread", headers=as_user(1)).json() == {"messages": 2}
    assert client.get("/api/me/unread", headers=as_user(2)).json() == {"messages": 0}          # my own messages are not «unread»
    client.get(f"/api/conversations/{cid}/messages", headers=as_user(1))                       # opening the chat reads them
    assert client.get("/api/me/unread", headers=as_user(1)).json() == {"messages": 0}


def test_search_relevance_puts_title_matches_first_and_finds_by_company_name(client):
    a = make(client, title="Повар", description="кухня и ещё слово сварщик")
    b = make(client, title="Сварщик на завод", description="завод")
    r = client.get("/api/catalog?q=сварщик&sort=relevance").json()["items"]
    assert [x["id"] for x in r][:2] == [b, a]                                                 # in the title beats in the text
    assert client.get("/api/catalog?q=сварщик&sort=relevance&limit=1&offset=1").json()["items"][0]["id"] == a


def test_versions_restore_and_price_hint(client):
    lid = make(client, title="Велосипед", description="Первое описание")
    client.put(f"/api/listings/{lid}", json={"title": "Велосипед горный", "description": "Второе описание подлиннее"}, headers=as_user(1))   # goes to moderation
    h = client.get(f"/api/listings/{lid}/history", headers=as_user(1)).json()
    versions = [e for e in h if e.get("version_id")]
    assert len(versions) == 1 and "Велосипед" in versions[0]["preview"]
    assert client.post(f"/api/listings/{lid}/restore/{versions[0]['version_id']}", headers=as_user(2)).status_code == 404
    r = client.post(f"/api/listings/{lid}/restore/{versions[0]['version_id']}", headers=as_user(1))
    assert r.status_code == 200
    assert client.post(f"/api/listings/{lid}/restore/9999", headers=as_user(1)).status_code == 404
    assert len([e for e in client.get(f"/api/listings/{lid}/history", headers=as_user(1)).json() if e.get("version_id")]) == 2    # the state before the return is kept too
    # price hint: needs five similar listings
    assert client.get("/api/price-hint?category=marketplace").json()["hint"] is None
    for i, price in enumerate((100000, 120000, 140000, 160000, 180000)):
        make(client, user=2 + i % 2, title=f"Товар {i}", price_amount=price)
    hint = client.get("/api/price-hint?category=marketplace").json()
    assert hint["n"] >= 5 and hint["hint"]["low"] <= hint["hint"]["median"] <= hint["hint"]["high"]
