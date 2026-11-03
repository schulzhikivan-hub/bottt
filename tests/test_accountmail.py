import pytest
from fastapi.testclient import TestClient

from app import accountmail, mailer
from app.database import engine
from app.main import app
from tests.tg import as_user, reset_db, accept_terms

SENT = []


@pytest.fixture()
def client(monkeypatch):
    assert engine.url.drivername == "sqlite"
    reset_db()
    SENT.clear()
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(mailer.settings, "smtp_from", "noreply@test")
    monkeypatch.setattr(accountmail.mailer, "send", lambda to, subject, text: SENT.append((to, subject, text)) or True)
    with TestClient(app) as c:
        accept_terms(c, 1)
        c.put("/api/me/account", json={"display_name": "Иван", "email": "ivan@example.com", "password": "Secret123"}, headers=as_user(1))
        yield c


def token_from_letter(param):
    text = SENT[-1][2]
    return text.split(f"?{param}=")[1].split()[0]


def test_mail_is_off_without_smtp_and_the_site_says_so(client, monkeypatch):
    monkeypatch.setattr(mailer.settings, "smtp_host", "")
    assert client.get("/api/mail-state").json() == {"enabled": False}
    assert client.post("/api/me/account/email-confirm/send", headers=as_user(1)).status_code == 503
    assert client.post("/api/auth/password-reset/request", json={"email": "ivan@example.com"}).status_code == 503


def test_email_is_confirmed_by_a_one_time_link(client):
    assert client.post("/api/me/account/email-confirm/send", headers=as_user(1)).json() == {"sent": True}
    tok = token_from_letter("confirm")
    assert client.post("/api/auth/email-confirm", json={"token": tok}).json() == {"ok": True}
    assert client.get("/api/me/account", headers=as_user(1)).json()["email_verified"] is True
    assert client.post("/api/auth/email-confirm", json={"token": tok}).status_code == 400                    # used once
    assert client.post("/api/me/account/email-confirm/send", headers=as_user(1)).status_code == 409           # already confirmed


def test_password_is_restored_only_for_a_confirmed_email_and_closes_other_sessions(client):
    client.post("/api/auth/password-reset/request", json={"email": "ivan@example.com"})
    assert SENT == []                                                           # not confirmed: no letter, but the same answer
    assert client.post("/api/auth/password-reset/request", json={"email": "nobody@example.com"}).json() == {"ok": True}
    assert SENT == []
    client.post("/api/me/account/email-confirm/send", headers=as_user(1))
    client.post("/api/auth/email-confirm", json={"token": token_from_letter("confirm")})
    SENT.clear()
    assert client.post("/api/auth/password-reset/request", json={"email": "ivan@example.com"}).json() == {"ok": True}
    assert len(SENT) == 1 and SENT[0][0] == "ivan@example.com"
    tok = token_from_letter("reset")
    assert client.post("/api/auth/password-reset", json={"token": tok, "password": "short"}).status_code == 400   # the usual password rules
    assert client.post("/api/auth/password-reset", json={"token": tok, "password": "NewSecret456"}).json() == {"ok": True}
    assert client.post("/api/auth/password-reset", json={"token": tok, "password": "NewSecret789"}).status_code == 400   # a token works once
    assert client.post("/api/auth/password-login", json={"email": "ivan@example.com", "password": "Secret123"}).status_code == 401
    assert client.post("/api/auth/password-login", json={"email": "ivan@example.com", "password": "NewSecret456"}).status_code == 200


def test_a_link_dies_when_the_email_is_changed(client):
    client.post("/api/me/account/email-confirm/send", headers=as_user(1))
    tok = token_from_letter("confirm")
    client.put("/api/me/account", json={"email": "other@example.com", "current_password": "Secret123"}, headers=as_user(1))
    assert client.post("/api/auth/email-confirm", json={"token": tok}).status_code == 400
