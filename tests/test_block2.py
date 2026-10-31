import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app import guard, photos
from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import Message, Notification, User
from app.payments import now
from tests.tg import accept_terms, as_user, reset_db

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    assert engine.url.drivername == "sqlite"
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
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


def png():
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "red").save(buf, "PNG")
    return buf.getvalue()


def chat(client):
    lid = client.post("/api/listings", json={"category": "marketplace", "title": "Стол", "city": "Сеул"}, headers=as_user(1)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]


# ---- feed and settings by kind ----

def test_every_notice_lands_in_the_feed_even_with_telegram_off(client):
    with SessionLocal() as db:
        guard.notify_user(db, "2", "Привет", "messages")   # NOTIFICATIONS_ENABLED=false in tests
    feed = client.get("/api/me/notifications", headers=as_user(2)).json()
    assert feed["unread"] == 1 and feed["items"][0]["text"] == "Привет" and feed["items"][0]["label"] == "Сообщения"
    assert client.get("/api/me", headers=as_user(2)).json()["unread"] == 1
    assert client.get("/api/me/notifications", headers=as_user(3)).json()["items"] == []  # nobody else sees it
    assert client.post("/api/me/notifications/read", json={}, headers=as_user(2)).json()["unread"] == 0


def test_mark_selected_only(client):
    with SessionLocal() as db:
        for t in ("a", "b"):
            guard.notify_user(db, "2", t, "system")
    items = client.get("/api/me/notifications", headers=as_user(2)).json()["items"]
    client.post("/api/me/notifications/read", json={"ids": [items[0]["id"]]}, headers=as_user(2))
    assert client.get("/api/me/notifications", headers=as_user(2)).json()["unread"] == 1


def test_telegram_follows_kind_and_master_switch(client, sent):
    with SessionLocal() as db:
        guard.notify_user(db, "2", "m1", "messages")
    assert [t[1] for t in sent] == ["m1"]
    client.put("/api/me/notify-prefs", json={"kinds": {"messages": False}}, headers=as_user(2))
    with SessionLocal() as db:
        guard.notify_user(db, "2", "m2", "messages")
        guard.notify_user(db, "2", "a1", "applications")
    assert [t[1] for t in sent] == ["m1", "a1"]            # messages off, applications still on
    client.put("/api/me/notify-prefs", json={"master": False}, headers=as_user(2))
    with SessionLocal() as db:
        guard.notify_user(db, "2", "a2", "applications")
    assert [t[1] for t in sent] == ["m1", "a1"]
    assert len(client.get("/api/me/notifications", headers=as_user(2)).json()["items"]) == 4  # feed keeps all


def test_prefs_validation_and_view(client):
    assert client.put("/api/me/notify-prefs", json={"kinds": {"nope": False}}, headers=as_user(2)).status_code == 422
    view = client.get("/api/me/notify-prefs", headers=as_user(2)).json()
    assert view["master"] is True and {k["key"] for k in view["kinds"]} == set(guard.NOTIFY_KINDS)


# ---- name and avatar ----

def test_display_name_rules_and_application_snapshot(client):
    assert client.put("/api/me/profile", json={"display_name": "Иван @ivan_kr"}, headers=as_user(2)).status_code == 400
    assert client.put("/api/me/profile", json={"display_name": "+82 10 1234 5678"}, headers=as_user(2)).status_code == 400
    assert client.put("/api/me/profile", json={"display_name": "  Иван   Петров "}, headers=as_user(2)).json()["display_name"] == "Иван Петров"
    lid = client.post("/api/listings", json={"category": "job", "title": "Повар", "city": "Сеул"}, headers=as_user(1)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    client.post(f"/api/listings/{lid}/apply", json={"contact": "@ivan1"}, headers=as_user(2))
    row = client.get(f"/api/listings/{lid}/applications", headers=as_user(1)).json()[0]
    assert row["name"] == "Иван Петров"


def test_avatar_upload_replace_remove(client):
    r = client.post("/api/me/avatar", files={"file": ("a.png", png(), "image/png")}, headers=as_user(2))
    assert r.status_code == 201
    url = r.json()["avatar"]
    img = Image.open(io.BytesIO(client.get(url).content))
    assert img.size == (256, 256)                       # square crop, metadata gone
    assert client.get("/api/me", headers=as_user(2)).json()["avatar"] == url
    r2 = client.post("/api/me/avatar", files={"file": ("a.png", png(), "image/png")}, headers=as_user(2))
    assert client.get(url).status_code == 404            # the old file is deleted
    assert client.delete("/api/me/avatar", headers=as_user(2)).json()["avatar"] is None
    assert client.get(r2.json()["avatar"]).status_code == 404
    assert client.post("/api/me/avatar", files={"file": ("a.txt", b"not an image", "text/plain")}, headers=as_user(2)).status_code == 400


# ---- chat: read receipts, photos, unread, status ----

def test_read_receipts_and_unread_counter(client):
    conv = chat(client)
    client.post(f"/api/conversations/{conv}/messages", json={"text": "привет"}, headers=as_user(2))
    assert client.get("/api/conversations", headers=as_user(1)).json()[0]["unread"] == 1
    mine = client.get(f"/api/conversations/{conv}/messages", headers=as_user(2)).json()
    assert mine[0]["read"] is False                      # the seller has not opened it yet
    client.get(f"/api/conversations/{conv}/messages", headers=as_user(1))
    assert client.get("/api/conversations", headers=as_user(1)).json()[0]["unread"] == 0
    assert client.get(f"/api/conversations/{conv}/messages", headers=as_user(2)).json()[0]["read"] is True
    # reading your own messages never marks them
    client.post(f"/api/conversations/{conv}/messages", json={"text": "ещё"}, headers=as_user(2))
    assert client.get(f"/api/conversations/{conv}/messages", headers=as_user(2)).json()[-1]["read"] is False


def test_chat_photo(client):
    conv = chat(client)
    r = client.post(f"/api/conversations/{conv}/photo", files={"file": ("p.png", png(), "image/png")}, headers=as_user(2))
    assert r.status_code == 201 and r.json()["photo"].endswith(".jpg") and r.json()["text"] == ""
    assert client.get(r.json()["photo"]).status_code == 200
    seen = client.get(f"/api/conversations/{conv}/messages", headers=as_user(1)).json()
    assert seen[0]["photo"] == r.json()["photo"]
    assert client.get("/api/conversations", headers=as_user(1)).json()[0]["last_text"] == "📷 Фото"
    assert client.post(f"/api/conversations/{conv}/photo", files={"file": ("p.png", png(), "image/png")}, headers=as_user(3)).status_code == 404
    assert client.post(f"/api/conversations/{conv}/photo", files={"file": ("x.txt", b"nope", "text/plain")}, headers=as_user(2)).status_code == 400


def test_chat_photo_blocked_pair_and_blocked_account(client):
    conv = chat(client)
    client.post("/api/users/1/block", headers=as_user(2))
    assert client.post(f"/api/conversations/{conv}/photo", files={"file": ("p.png", png(), "image/png")}, headers=as_user(2)).status_code == 403


def test_status_is_coarse_and_private(client):
    conv = chat(client)
    assert client.get(f"/api/conversations/{conv}/status", headers=as_user(2)).json()["status"] == ""  # seller never seen yet
    client.get("/api/me", headers=as_user(1))
    assert client.get(f"/api/conversations/{conv}/status", headers=as_user(2)).json()["status"] == "online"
    import datetime as dt
    with SessionLocal() as db:
        db.get(User, "1").last_seen_at = now() - dt.timedelta(hours=3)
        db.commit()
    assert client.get(f"/api/conversations/{conv}/status", headers=as_user(2)).json()["status"] == "recently"
    with SessionLocal() as db:
        db.get(User, "1").last_seen_at = now() - dt.timedelta(days=3)
        db.commit()
    assert client.get(f"/api/conversations/{conv}/status", headers=as_user(2)).json()["status"] == ""
    assert client.get(f"/api/conversations/{conv}/status", headers=as_user(3)).status_code == 404


def test_new_chat_message_creates_feed_notice_for_the_other_side(client):
    conv = chat(client)
    client.post(f"/api/conversations/{conv}/messages", json={"text": "здравствуйте"}, headers=as_user(2))
    items = client.get("/api/me/notifications", headers=as_user(1)).json()["items"]
    assert any(i["kind"] == "messages" and "здравствуйте" in i["text"] for i in items)
