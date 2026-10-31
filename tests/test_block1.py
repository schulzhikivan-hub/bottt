import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import admin_ops as ops
from app import guard, lifecycle
from app.database import SessionLocal, engine
from app.main import app
from app.models import Application, Listing, Report, User
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


@pytest.fixture()
def sent(monkeypatch):
    log = []
    monkeypatch.setattr(guard.settings, "notifications_enabled", True)
    monkeypatch.setattr(guard, "send_telegram", lambda tg_id, text: log.append((str(tg_id), text)))
    return log


def vacancy(client, user=1, category="job"):
    lid = client.post("/api/listings", json={"category": category, "title": "Повар", "city": "Сеул", "description": "ok"},
                      headers=as_user(user)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def apply(client, lid, user=2):
    return client.post(f"/api/listings/{lid}/apply", json={"contact": "@ivan1", "message": "Здравствуйте"}, headers=as_user(user))


def test_withdraw_and_apply_again(client):
    lid = vacancy(client)
    assert apply(client, lid).status_code == 201
    aid = client.get("/api/me/applications", headers=as_user(2)).json()[0]["id"]
    assert client.post(f"/api/applications/{aid}/withdraw", headers=as_user(3)).status_code == 404
    assert client.post(f"/api/applications/{aid}/withdraw", headers=as_user(2)).status_code == 200
    assert client.post(f"/api/applications/{aid}/withdraw", headers=as_user(2)).status_code == 409
    assert client.get(f"/api/listings/{lid}/applications", headers=as_user(1)).json() == []  # employer no longer sees it
    assert client.get("/api/me/listings", headers=as_user(1)).json()[0]["applications"]["total"] == 0
    assert apply(client, lid).status_code == 201  # the same person can reply again
    assert client.get("/api/me/applications", headers=as_user(2)).json()[0]["status"] == "new"


def test_employer_reply_reaches_applicant(client, sent):
    lid = vacancy(client)
    apply(client, lid)
    aid = client.get(f"/api/listings/{lid}/applications", headers=as_user(1)).json()[0]["id"]
    r = client.put(f"/api/applications/{aid}", json={"status": "invited", "reply": "Приходите в среду в 10:00"}, headers=as_user(1))
    assert r.status_code == 200
    mine = client.get("/api/me/applications", headers=as_user(2)).json()[0]
    assert mine["reply"] == "Приходите в среду в 10:00" and mine["status"] == "invited"
    assert any(t[0] == "2" and "в среду" in t[1] for t in sent)
    assert client.get(f"/api/listings/{lid}/applications", headers=as_user(1)).json()[0]["reply"].startswith("Приходите")
    client.post(f"/api/applications/{aid}/withdraw", headers=as_user(2))
    assert client.put(f"/api/applications/{aid}", json={"status": "rejected"}, headers=as_user(1)).status_code == 409


def test_applicants_told_when_vacancy_ends(client, sent):
    lid = vacancy(client)
    apply(client, lid, 2)
    apply(client, lid, 3)
    aid = client.get("/api/me/applications", headers=as_user(3)).json()[0]["id"]
    client.post(f"/api/applications/{aid}/withdraw", headers=as_user(3))
    sent.clear()
    assert client.post(f"/api/listings/{lid}/close", headers=as_user(1)).status_code == 200
    assert [t[0] for t in sent] == ["2"] and "закрыта" in sent[0][1]  # the withdrawn one is not bothered


def test_applicants_told_on_hide_and_housing_is_silent(client, sent):
    lid = vacancy(client)
    apply(client, lid)
    sent.clear()
    client.post(f"/api/admin/listings/{lid}/hide?reason=подозрение", headers=ADMIN)
    assert any(t[0] == "2" and "скрыта" in t[1] for t in sent)
    flat = vacancy(client, category="housing")
    sent.clear()
    client.post(f"/api/listings/{flat}/close", headers=as_user(1))
    assert sent == []


def test_two_expiry_reminders_then_end(client):
    lid = vacancy(client)
    with SessionLocal() as db:
        item = db.get(Listing, lid)
        item.expires_at = now() + dt.timedelta(days=2)
        db.commit()
        assert [x.id for x in lifecycle.expiring_soon(db)] == [lid]
        assert lifecycle.expiring_soon(db) == []          # no repeats
        assert lifecycle.expiring_tomorrow(db) == []      # not yet within a day
        item.expires_at = now() + dt.timedelta(hours=20)
        db.commit()
        assert [x.id for x in lifecycle.expiring_tomorrow(db)] == [lid]
        assert lifecycle.expiring_tomorrow(db) == []
        lifecycle.renew(item)
        assert item.expiry_notified is False and item.expiry_notified_1d is False


def test_reporter_and_author_hear_the_decision(client, sent):
    lid = vacancy(client)
    client.post("/api/reports", json={"target_type": "listing", "target_id": str(lid), "reason": "spam"}, headers=as_user(3))
    rid = client.get("/api/admin/reports", headers=ADMIN).json()[0]["id"]
    sent.clear()
    assert client.post(f"/api/admin/reports/{rid}/resolve", headers=ADMIN).status_code == 200
    who = {t[0]: t[1] for t in sent}
    assert "приняли меры" in who["3"] and "скрыто по жалобам" in who["1"]
    client.post("/api/reports", json={"target_type": "listing", "target_id": str(vacancy(client)), "reason": "spam"}, headers=as_user(3))
    rid2 = client.get("/api/admin/reports", headers=ADMIN).json()[0]["id"]
    sent.clear()
    client.post(f"/api/admin/reports/{rid2}/reject", headers=ADMIN)
    assert "нарушений не нашли" in sent[0][1] and sent[0][0] == "3"


def test_block_hides_listings_and_unblock_restores(client):
    live = vacancy(client)
    draft = client.post("/api/listings", json={"category": "job", "title": "Черновик", "city": "Сеул", "draft": True}, headers=as_user(1)).json()["id"]
    with SessionLocal() as db:
        ops.block_user(db, "999", "1", "Мошенничество")
    assert client.get(f"/api/listings/{live}").status_code == 404
    with SessionLocal() as db:
        assert db.get(Listing, live).status == "hidden" and db.get(Listing, draft).status == "draft"
        ops.unblock_user(db, "999", "1", "Ошибка")
        assert db.get(Listing, live).status == "approved"
    assert client.get(f"/api/listings/{live}").status_code == 200


def test_unblock_does_not_revive_listing_hidden_by_moderator(client):
    lid = vacancy(client)
    client.post(f"/api/admin/listings/{lid}/hide?reason=нарушение", headers=ADMIN)
    with SessionLocal() as db:
        ops.block_user(db, "999", "1", "Причина блокировки")
        ops.unblock_user(db, "999", "1", "Разблокировка")
        assert db.get(Listing, lid).status == "hidden"


def test_blocked_person_cannot_receive_messages(client):
    lid = vacancy(client)
    conv = client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]
    assert client.post(f"/api/conversations/{conv}/messages", json={"text": "привет"}, headers=as_user(2)).status_code == 201
    with SessionLocal() as db:
        ops.block_user(db, "999", "1", "Причина блокировки")
    assert client.post(f"/api/conversations/{conv}/messages", json={"text": "ещё"}, headers=as_user(2)).status_code == 403
    assert client.post(f"/api/conversations/{conv}/messages", json={"text": "я"}, headers=as_user(1)).status_code == 403


def test_blocked_applicant_is_not_shown_to_employer(client):
    lid = vacancy(client)
    apply(client, lid)
    with SessionLocal() as db:
        ops.block_user(db, "999", "2", "Спам в откликах")
    assert client.get(f"/api/listings/{lid}/applications", headers=as_user(1)).json() == []


def test_messages_paginate(client):
    lid = vacancy(client)
    conv = client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]
    for i in range(7):
        client.post(f"/api/conversations/{conv}/messages", json={"text": f"м{i}"}, headers=as_user(2))
    last = client.get(f"/api/conversations/{conv}/messages?limit=3", headers=as_user(2)).json()
    assert [m["text"] for m in last] == ["м4", "м5", "м6"]
    older = client.get(f"/api/conversations/{conv}/messages?limit=3&before_id={last[0]['id']}", headers=as_user(2)).json()
    assert [m["text"] for m in older] == ["м1", "м2", "м3"]
    rest = client.get(f"/api/conversations/{conv}/messages?limit=3&before_id={older[0]['id']}", headers=as_user(2)).json()
    assert [m["text"] for m in rest] == ["м0"]
