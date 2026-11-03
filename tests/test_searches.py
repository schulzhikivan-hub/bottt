import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import searches
from app.database import SessionLocal, engine
from app.main import app
from app.models import SavedSearch
from app.payments import now
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2):
            accept_terms(c, uid)
        yield c


def post(client, title, category="job", user=1):
    lid = client.post("/api/listings", json={"category": category, "title": title, "city": "Сеул", "description": "ok"},
                      headers=as_user(user)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def run():
    with SessionLocal() as db:
        return searches.new_matches(db)


def test_save_list_delete(client):
    r = client.post("/api/me/searches", json={"category": "job", "q": "повар"}, headers=as_user(2))
    assert r.status_code == 201
    assert client.get("/api/me/searches", headers=as_user(2)).json()[0]["params"] == {"category": "job", "q": "повар"}
    assert client.delete(f"/api/me/searches/{r.json()['id']}", headers=as_user(1)).status_code == 404
    assert client.delete(f"/api/me/searches/{r.json()['id']}", headers=as_user(2)).status_code == 200
    assert client.post("/api/me/searches", json={}, headers=as_user(2)).status_code == 400


def test_limit(client):
    for i in range(10):
        assert client.post("/api/me/searches", json={"q": f"w{i}"}, headers=as_user(2)).status_code == 201
    assert client.post("/api/me/searches", json={"q": "extra"}, headers=as_user(2)).status_code == 409


def test_notifies_only_about_new_matching_listings(client):
    post(client, "Повар старый")  # published before the search was saved
    client.post("/api/me/searches", json={"category": "job", "q": "повар"}, headers=as_user(2))
    assert run() == []
    with SessionLocal() as db:  # pretend the last check was an hour ago
        for s in db.query(SavedSearch):
            s.last_checked_at = now() - dt.timedelta(hours=1)
        db.commit()
    post(client, "Повар новый")
    post(client, "Диван", category="marketplace")
    out = run()
    assert len(out) == 1 and out[0][0] == "2" and "Повар новый" in out[0][1]
    assert run() == []  # reported once


def test_own_listings_do_not_notify_and_switch_off_respected(client):
    client.post("/api/me/searches", json={"q": "повар"}, headers=as_user(1))
    with SessionLocal() as db:
        for s in db.query(SavedSearch):
            s.last_checked_at = now() - dt.timedelta(hours=1)
        db.commit()
    post(client, "Повар мой", user=1)
    assert run() == []
