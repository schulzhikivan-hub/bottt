import pytest
from fastapi.testclient import TestClient

from app import guard
from app.database import engine
from app.main import app
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}
BASE = {"category": "marketplace", "title": "Велосипед", "city": "Сеул",
        "price": "50 000 ₩", "description": "Почти новый", "contact": "@seller"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def post(client, user=1, **changes):
    return client.post("/api/listings", json={**BASE, **changes}, headers=as_user(user))


def test_prepayment_and_card_transfer_blocked(client):
    assert post(client, description="Нужна предоплата 10 000").status_code == 400
    assert post(client, description="Переведите на карту 1000").status_code == 400


def test_phone_and_links_in_description_blocked(client):
    assert post(client, description="Звоните 010-1234-5678").status_code == 400
    assert post(client, description="Смотрите https://example.com").status_code == 400
    assert post(client, description="Пишите @seller_kr").status_code == 400
    # Цены и обычные числа не считаются телефоном
    assert post(client, description="Цена 2 800 000 ₩, самовывоз").status_code == 201


def test_duplicate_is_journaled_for_moderator(client):
    post(client, user=1)
    post(client, user=2)
    log = client.get("/api/admin/audit", headers=ADMIN).json()
    assert any(x["action"] == "listing.duplicate_suspected" and x["actor"] == "system" for x in log)


def test_listing_creation_rate_limited(client):
    # Три раздела по пять объявлений: лимит 10 в час срабатывает раньше лимита раздела
    cats = ["job", "housing", "marketplace"]
    for i in range(10):
        assert post(client, category=cats[i % 3], title=f"Объявление {i}").status_code == 201
    assert post(client, category="job", title="Объявление 11").status_code == 429


def test_message_triggers_notification_to_recipient(client, monkeypatch):
    sent = []
    monkeypatch.setattr(guard, "settings", type("S", (), {
        "notifications_enabled": True, "bot_token": "123456:TEST", "webapp_url": ""})())
    monkeypatch.setattr(guard.httpx, "post", lambda url, json, timeout: sent.append(json) or None)
    lid = post(client, user=1).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    conv = client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]
    client.post(f"/api/conversations/{conv}/messages", json={"text": "Здравствуйте"}, headers=as_user(2))
    assert sent and sent[0]["chat_id"] == 1
    assert "Здравствуйте" in sent[0]["text"]


def test_user_can_turn_off_message_notifications(client, monkeypatch):
    sent = []
    monkeypatch.setattr(guard, "settings", type("S", (), {
        "notifications_enabled": True, "bot_token": "123456:TEST", "webapp_url": ""})())
    monkeypatch.setattr(guard.httpx, "post", lambda url, json, timeout: sent.append(json) or None)
    assert client.put("/api/me/notifications", json={"messages": False}, headers=as_user(1)).json() == {"messages": False}
    lid = post(client, user=1).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    conv = client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]
    client.post(f"/api/conversations/{conv}/messages", json={"text": "Привет"}, headers=as_user(2))
    assert sent == []
