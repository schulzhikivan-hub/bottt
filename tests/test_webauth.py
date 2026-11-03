import pytest
from fastapi.testclient import TestClient

from app import payments, webauth
from app.database import engine, SessionLocal
from app.main import app
from tests.tg import reset_db, as_user, make_init_data

ADMIN_ID = "999"  # super administrator created by reset_db


@pytest.fixture()
def client(monkeypatch):
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    reset_db()
    webauth._username.clear()
    monkeypatch.setattr(payments, "telegram_call", lambda method, payload: {"username": "test_bot"})
    with TestClient(app) as c:
        yield c


def start(client):
    r = client.post("/api/auth/start", headers={"user-agent": "TestBrowser/1.0", "x-forwarded-for": "203.0.113.7"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    return r.json()


def confirm(token, tg_id):
    with SessionLocal() as db:
        return webauth.confirm_login(db, token, tg_id)


def test_start_returns_bot_link(client):
    data = start(client)
    assert data["url"] == f"https://t.me/test_bot?start=login_{data['token']}"


def test_poll_waits_until_confirmed(client):
    token = start(client)["token"]
    r = client.get(f"/api/auth/poll/{token}")
    assert r.json() == {"status": "pending"}
    assert r.headers["cache-control"] == "no-store, no-cache, must-revalidate"


def test_confirmed_login_gives_user_session_that_works(client):
    token = start(client)["token"]
    assert confirm(token, "4242")
    response = client.get(f"/api/auth/poll/{token}")
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    data = response.json()
    assert data["status"] == "ok" and data["tg_id"] == "4242"
    assert "admin_session" not in data  # an ordinary user gets no admin access
    headers = {"X-Web-Session": data["session"]}
    assert client.get("/api/me", headers=headers).json()["tg_id"] == "4242"
    # the website session can do what the Mini App can: accept terms, then publish
    assert client.post("/api/me/consent", json={"accept": True}, headers=headers).status_code == 200
    listing = {"category": "marketplace", "title": "Диван", "city": "Сеул", "price": "1", "description": "ok", "contact": ""}
    assert client.post("/api/listings", json=listing, headers=headers).status_code == 201


def test_session_is_handed_over_once(client):
    token = start(client)["token"]
    confirm(token, "4242")
    assert client.get(f"/api/auth/poll/{token}").json()["status"] == "ok"
    assert client.get(f"/api/auth/poll/{token}").json() == {"status": "expired"}


def test_rejected_login_gives_nothing(client):
    token = start(client)["token"]
    with SessionLocal() as db:
        assert webauth.reject_login(db, token)
    assert client.get(f"/api/auth/poll/{token}").json() == {"status": "rejected"}
    assert confirm(token, "4242") is False  # cannot be confirmed afterwards


def test_unknown_or_forged_token(client):
    assert client.get("/api/auth/poll/not-a-real-token").json() == {"status": "expired"}
    assert confirm("not-a-real-token", "4242") is False


def test_expired_request_cannot_be_confirmed(client):
    token = start(client)["token"]
    with SessionLocal() as db:
        from app.models import LoginRequest
        import datetime as dt
        row = db.get(LoginRequest, webauth._hash(token))
        row.expires_at = payments.now() - dt.timedelta(minutes=1)
        db.commit()
    assert confirm(token, "4242") is False
    assert client.get(f"/api/auth/poll/{token}").json() == {"status": "expired"}


def test_request_details_are_kept_for_the_confirmation_screen(client):
    token = start(client)["token"]
    with SessionLocal() as db:
        row = webauth.login_request(db, token)
        assert row.ip == "203.0.113.7" and row.agent == "TestBrowser/1.0"


def test_admin_confirming_in_bot_gets_admin_session_too(client):
    token = start(client)["token"]
    confirm(token, ADMIN_ID)
    data = client.get(f"/api/auth/poll/{token}").json()
    assert data["role"] == "super_admin"
    assert client.get("/api/admin/me", headers={"X-Admin-Session": data["admin_session"]}).json()["role"] == "super_admin"


def test_web_logout_ends_session(client):
    token = start(client)["token"]
    confirm(token, "4242")
    session = client.get(f"/api/auth/poll/{token}").json()["session"]
    client.post("/api/auth/logout", headers={"X-Web-Session": session})
    assert client.get("/api/me", headers={"X-Web-Session": session}).status_code == 401


def test_unknown_web_session_is_refused(client):
    assert client.get("/api/me", headers={"X-Web-Session": "bogus"}).status_code == 401
    assert client.get("/api/me").status_code == 401


def test_admin_opened_inside_telegram_logs_in_automatically(client):
    r = client.post("/api/admin/auth/telegram", headers=as_user(int(ADMIN_ID)))
    assert r.status_code == 200
    token = r.json()["token"]
    assert client.get("/api/admin/me", headers={"X-Admin-Session": token}).json()["tg_id"] == ADMIN_ID


def test_non_admin_cannot_auto_login(client):
    assert client.post("/api/admin/auth/telegram", headers=as_user(31337)).status_code == 403


def test_forged_init_data_cannot_auto_login(client):
    forged = {"X-Telegram-Init-Data": make_init_data(int(ADMIN_ID), bot_token="1:WRONG")}
    assert client.post("/api/admin/auth/telegram", headers=forged).status_code == 401
    assert client.post("/api/admin/auth/telegram").status_code == 401
