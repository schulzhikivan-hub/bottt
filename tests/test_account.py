import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal, engine
from app.main import app
from app.models import User, WebSession
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


def setup(client, uid=1, email="Ivan@Example.com", password="Secret123", name="Иван"):
    return client.put("/api/me/account", json={"display_name": name, "email": email, "password": password}, headers=as_user(uid))


def test_first_sign_in_asks_for_details_until_they_are_set_or_postponed(client):
    me = client.get("/api/me", headers=as_user(1)).json()
    assert me["account"]["needs_setup"] is True
    assert client.post("/api/me/account/skip", headers=as_user(1)).json()["needs_setup"] is False      # «заполню позже»
    r = setup(client)
    assert r.status_code == 200 and r.json()["email"] == "ivan@example.com" and r.json()["has_password"] is True
    assert r.json()["name"] == "Иван" and r.json()["needs_setup"] is False
    with SessionLocal() as db:
        u = db.get(User, "1")
        assert u.password_hash.startswith("pbkdf2_sha256$") and "Secret123" not in u.password_hash


def test_validation_of_email_password_and_name(client):
    assert setup(client, email="not-an-email").status_code == 400
    assert setup(client, password="short1").status_code == 400
    assert setup(client, password="onlyletters").status_code == 400
    assert setup(client, password="12345678").status_code == 400
    assert setup(client, name="Иван @ivan_kr").status_code == 400
    assert setup(client).status_code == 200
    assert setup(client, uid=2).status_code == 409                       # the e-mail is taken


def test_password_login_gives_a_website_session_and_hides_who_exists(client):
    setup(client)
    ok = client.post("/api/auth/password-login", json={"email": "IVAN@example.com", "password": "Secret123"})
    assert ok.status_code == 200 and ok.json()["tg_id"] == "1"
    session = ok.json()["session"]
    assert client.get("/api/me", headers={"X-Web-Session": session}).status_code == 200
    wrong = client.post("/api/auth/password-login", json={"email": "ivan@example.com", "password": "Wrong1234"})
    unknown = client.post("/api/auth/password-login", json={"email": "nobody@example.com", "password": "Wrong1234"})
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json()           # the same answer


def test_ten_failures_lock_and_a_blocked_person_can_not_sign_in(client):
    setup(client)
    from app.models import RateHit

    def forget_limits():
        with SessionLocal() as db:
            db.query(RateHit).delete()
            db.commit()
    for _ in range(10):
        forget_limits()                                                  # the limiter is tested elsewhere: here the 10-in-a-row lock
        client.post("/api/auth/password-login", json={"email": "ivan@example.com", "password": "Wrong1234"})
    forget_limits()
    locked = client.post("/api/auth/password-login", json={"email": "ivan@example.com", "password": "Secret123"})
    assert locked.status_code == 429                                     # even the right password waits
    with SessionLocal() as db:
        u = db.get(User, "1")
        u.pw_locked_until = None
        u.blocked = True
        db.commit()
    forget_limits()
    assert client.post("/api/auth/password-login", json={"email": "ivan@example.com", "password": "Secret123"}).status_code == 403


def test_changing_password_needs_the_old_one_and_closes_other_sessions(client):
    setup(client)
    session = client.post("/api/auth/password-login", json={"email": "ivan@example.com", "password": "Secret123"}).json()["session"]
    other = client.post("/api/auth/password-login", json={"email": "ivan@example.com", "password": "Secret123"}).json()["session"]
    assert client.put("/api/me/account", json={"password": "NewSecret456"}, headers=as_user(1)).status_code == 403
    r = client.put("/api/me/account", json={"password": "NewSecret456", "current_password": "Secret123"},
                   headers={**as_user(1), "X-Web-Session": session})
    assert r.status_code == 200
    with SessionLocal() as db:
        assert db.query(WebSession).count() == 1                          # only the browser that changed it stays
    assert client.post("/api/auth/password-login", json={"email": "ivan@example.com", "password": "Secret123"}).status_code == 401
    assert client.post("/api/auth/password-login", json={"email": "ivan@example.com", "password": "NewSecret456"}).status_code == 200
    assert other


def test_admin_sees_the_state_and_can_reset(client):
    setup(client)
    card = client.get("/api/admin/users/1/card", headers=ADMIN).json()
    assert card["email"] == "ivan@example.com" and card["has_password"] is True
    assert client.post("/api/admin/users/1/account-reset", json={"what": "password", "reason": "x"}, headers=ADMIN).status_code == 422
    r = client.post("/api/admin/users/1/account-reset", json={"what": "password", "reason": "потерял доступ"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["has_password"] is False and r.json()["email"] == "ivan@example.com"
    assert client.post("/api/auth/password-login", json={"email": "ivan@example.com", "password": "Secret123"}).status_code == 401
    client.post("/api/admin/users/1/account-reset", json={"what": "both", "reason": "чужой e-mail"}, headers=ADMIN)
    assert client.get("/api/admin/users-list?q=ivan@example.com", headers=ADMIN).json()["total"] == 0
