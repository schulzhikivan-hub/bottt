import pytest
from fastapi.testclient import TestClient

from app.database import engine
from app.main import app
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}
ITEM = {"category": "marketplace", "title": "Стол письменный", "city": "Сеул",
        "price": "50 000 ₩", "description": "Хорошее состояние", "contact": "@seller"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2):
            accept_terms(c, uid)
        yield c


def publish(client, user=1, category="marketplace"):
    body = {**ITEM, "category": category}
    lid = client.post("/api/listings", json=body, headers=as_user(user)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def test_admin_needs_session(client):
    r = client.get("/api/admin/listings")
    assert r.status_code == 401


def test_edit_sends_listing_back_to_moderation(client):
    lid = publish(client)
    r = client.put(f"/api/listings/{lid}", json={"price": "40 000 ₩"}, headers=as_user(1))
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    assert all(x["id"] != lid for x in client.get("/api/listings").json())


def test_only_owner_can_edit(client):
    lid = publish(client)
    assert client.put(f"/api/listings/{lid}", json={"price": "1"}, headers=as_user(2)).status_code == 404


def test_close_sold_reopen_flow(client):
    lid = publish(client)
    assert client.post(f"/api/listings/{lid}/sold", headers=as_user(1)).json()["status"] == "sold"
    assert all(x["id"] != lid for x in client.get("/api/listings").json())
    assert client.post(f"/api/listings/{lid}/reopen", headers=as_user(1)).json()["status"] == "pending"


def test_sold_only_for_marketplace(client):
    lid = publish(client, category="job")
    assert client.post(f"/api/listings/{lid}/sold", headers=as_user(1)).status_code == 400


def test_archive_removes_listing_for_owner(client):
    lid = publish(client)
    client.post(f"/api/listings/{lid}/archive", headers=as_user(1))
    assert client.put(f"/api/listings/{lid}", json={"price": "1"}, headers=as_user(1)).status_code == 404


def test_needs_fix_requires_comment_and_is_visible_to_author(client):
    lid = client.post("/api/listings", json=ITEM, headers=as_user(1)).json()["id"]
    bad = client.post(f"/api/admin/listings/{lid}/needs_fix", headers=ADMIN)
    assert bad.status_code == 400
    ok = client.post(f"/api/admin/listings/{lid}/needs_fix?reason=Добавьте+фото", headers=ADMIN)
    assert ok.json()["status"] == "needs_fix"
    mine = client.get("/api/me/listings", headers=as_user(1)).json()
    assert mine[0]["status"] == "needs_fix"


def test_moderation_actions_are_journaled(client):
    lid = client.post("/api/listings", json=ITEM, headers=as_user(1)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/reject?reason=Не+то", headers=ADMIN)
    log = client.get("/api/admin/audit", headers=ADMIN).json()
    assert log[0]["actor"] == "999"
    assert log[0]["action"] == "listing.reject"
    assert log[0]["object_id"] == str(lid)


def test_block_requires_reason_and_stops_writing(client):
    assert client.post("/api/admin/users/2/block", json={"reason": "x"}, headers=ADMIN).status_code == 422
    r = client.post("/api/admin/users/2/block", json={"reason": "Мошенничество"}, headers=ADMIN)
    assert r.status_code == 200
    assert client.post("/api/listings", json=ITEM, headers=as_user(2)).status_code == 403
    client.post("/api/admin/users/2/unblock", json={"reason": "Ошибка проверки"}, headers=ADMIN)
    assert client.post("/api/listings", json=ITEM, headers=as_user(2)).status_code == 201


def test_employer_application_and_decision(client):
    body = {"company_name": "Samin Motors", "registration_no": "123-45-67890", "contact_person": "Ким"}
    assert client.post("/api/me/employer", json=body, headers=as_user(1)).json()["employer_status"] == "pending"
    assert client.post("/api/me/employer", json=body, headers=as_user(1)).status_code == 409
    client.post("/api/admin/employers/1/approve", headers=ADMIN)
    assert client.post("/api/me/employer", json=body, headers=as_user(1)).status_code == 409


def test_settings_change_city(client):
    r = client.put("/api/me/settings", json={"city": "Пусан"}, headers=as_user(1))
    assert r.json()["city"] == "Пусан"
