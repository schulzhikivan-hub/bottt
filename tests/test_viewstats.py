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


def make(client, **fields):
    body = {"category": "marketplace", "title": "Велосипед", "city": "Сеул", "description": "ok", "contact": "", "price_amount": 100000} | fields
    lid = client.post("/api/listings", json=body, headers=as_user(1)).json()["id"]
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
