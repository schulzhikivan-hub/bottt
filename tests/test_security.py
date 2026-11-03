import pytest
from fastapi.testclient import TestClient

from app import rules
from app.database import SessionLocal, engine
from app.main import app
from app.models import AuditLog, Notification
from tests.tg import as_user, reset_db, accept_terms
from tests.test_catalog import make

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    rules.reset_cache()
    with TestClient(app) as c:
        for uid in (1, 2):
            accept_terms(c, uid)
        yield c
    rules.reset_cache()


def test_every_look_at_personal_data_is_written_down_and_listed(client):
    lid = make(client, user=1)
    client.get(f"/api/admin/listings/{lid}", headers=ADMIN)
    client.get("/api/admin/users/1/card", headers=ADMIN)
    r = client.get("/api/admin/security/pii-views", headers=ADMIN).json()
    assert {(i["kind"], i["id"]) for i in r["items"]} == {("listing", str(lid)), ("user", "1")} and r["by_staff"][0]["views"] == 2
    assert client.get("/api/admin/security/pii-views").status_code == 401
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(action="pii.list").count() == 1             # even reading the list is journaled


def test_too_many_views_alert_the_super_admins(client):
    lid = make(client, user=1)
    client.put("/api/admin/rules/security.pii_per_hour", json={"value": 5}, headers=ADMIN)
    rules.reset_cache()
    for _ in range(7):
        client.get(f"/api/admin/listings/{lid}", headers=ADMIN)
    with SessionLocal() as db:
        assert db.query(Notification).filter(Notification.text.like("%открыл личные данные%")).count() >= 1


def test_feature_flags_switch_without_a_release(client):
    flags = {f["key"]: f["state"] for f in client.get("/api/admin/flags", headers=ADMIN).json()}
    assert flags["company"] == "all"
    client.put("/api/admin/flags/company", json={"state": "off"}, headers=ADMIN)
    body = {"name": "Кафе", "reg_no": "123-45-67890"}
    assert client.post("/api/company", json=body, headers=as_user(1)).status_code == 403
    client.put("/api/admin/flags/company", json={"state": "all"}, headers=ADMIN)
    assert client.post("/api/company", json=body, headers=as_user(1)).status_code == 201
    assert client.put("/api/admin/flags/nope", json={"state": "all"}, headers=ADMIN).status_code == 400
    client.put("/api/admin/flags/ads", json={"state": "off"}, headers=ADMIN)
    client.post("/api/admin/ads", json={"title": "Скидка", "link": "https://good.com/x"}, headers=ADMIN)
    assert client.get("/api/ads?organic=10", headers=as_user(1)).json() == []
    with SessionLocal() as db:
        assert [a.details for a in db.query(AuditLog).filter_by(action="flag.change").order_by(AuditLog.id)][:2] == ["all → off", "off → all"]


def test_password_login_can_be_switched_off(client):
    client.put("/api/me/account", json={"email": "a@b.co", "password": "Secret123"}, headers=as_user(1))
    client.put("/api/admin/flags/pwlogin", json={"state": "off"}, headers=ADMIN)
    assert client.post("/api/auth/password-login", json={"email": "a@b.co", "password": "Secret123"}).status_code == 403
    client.put("/api/admin/flags/pwlogin", json={"state": "all"}, headers=ADMIN)
    assert client.post("/api/auth/password-login", json={"email": "a@b.co", "password": "Secret123"}).status_code == 200
