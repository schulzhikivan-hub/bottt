import pytest
from fastapi.testclient import TestClient

from app import guard
from app.database import engine
from app.main import app
from tests.tg import reset_db, accept_terms, TEST_ADMIN_SESSION

SUPER = {"X-Admin-Session": TEST_ADMIN_SESSION}


@pytest.fixture()
def client(monkeypatch):
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    reset_db()
    sent = []
    monkeypatch.setattr(guard, "send_telegram", lambda tg, text: sent.append((tg, text)) or True)
    with TestClient(app) as c:
        c.sent = sent
        yield c


def code_from(client):
    return client.sent[-1][1].split(": ")[1].split("\n")[0]


def login(client, tg):
    client.post("/api/admin/auth/request", json={"tg_id": tg})
    return client.post("/api/admin/auth/verify", json={"tg_id": tg, "code": code_from(client)})


def test_code_is_sent_to_telegram_for_admin_only(client):
    client.post("/api/admin/auth/request", json={"tg_id": "999"})
    assert client.sent and client.sent[-1][0] == "999"
    before = len(client.sent)
    r = client.post("/api/admin/auth/request", json={"tg_id": "424242"})
    assert r.status_code == 200 and r.json()["sent"] is True  # same answer for everyone
    assert len(client.sent) == before  # but no message to a non-admin


def test_correct_code_gives_session(client):
    r = login(client, "999")
    assert r.status_code == 200
    assert r.json()["role"] == "super_admin"
    token = r.json()["token"]
    assert client.get("/api/admin/me", headers={"X-Admin-Session": token}).json()["role"] == "super_admin"


def test_wrong_code_refused_and_counted(client):
    client.post("/api/admin/auth/request", json={"tg_id": "999"})
    r = client.post("/api/admin/auth/verify", json={"tg_id": "999", "code": "000000"})
    assert r.status_code == 401


def test_code_is_single_use(client):
    login(client, "999")
    again = client.post("/api/admin/auth/verify", json={"tg_id": "999", "code": code_from(client)})
    assert again.status_code == 401


def test_session_logout(client):
    token = login(client, "999").json()["token"]
    client.post("/api/admin/auth/logout", headers={"X-Admin-Session": token})
    assert client.get("/api/admin/me", headers={"X-Admin-Session": token}).status_code == 401


def test_moderator_cannot_refund_or_change_prices(client):
    client.put("/api/admin/admins/555", json={"role": "moderator"}, headers=SUPER)
    token = login(client, "555").json()["token"]
    mod = {"X-Admin-Session": token}
    assert client.get("/api/admin/listings", headers=mod).status_code == 200
    assert client.get("/api/admin/pricing", headers=mod).status_code == 403
    assert client.post("/api/admin/payments/1/refund", headers=mod).status_code == 403
    assert client.get("/api/admin/audit", headers=mod).status_code == 403


def test_only_super_admin_manages_staff(client):
    client.put("/api/admin/admins/555", json={"role": "admin"}, headers=SUPER)
    token = login(client, "555").json()["token"]
    r = client.put("/api/admin/admins/777", json={"role": "moderator"}, headers={"X-Admin-Session": token})
    assert r.status_code == 403


def test_super_admin_cannot_remove_own_access(client):
    r = client.put("/api/admin/admins/999", json={"role": "moderator"}, headers=SUPER)
    assert r.status_code == 400


def test_disabled_admin_cannot_log_in(client):
    client.put("/api/admin/admins/555", json={"role": "moderator", "active": False}, headers=SUPER)
    client.post("/api/admin/auth/request", json={"tg_id": "555"})
    assert client.sent == [] or all(tg != "555" for tg, _ in client.sent)


def test_login_writes_journal(client):
    login(client, "999")
    log = client.get("/api/admin/audit", headers=SUPER).json()
    assert any(x["action"] == "auth.login" and x["actor"] == "999" for x in log)
