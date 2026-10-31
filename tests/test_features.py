import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app
from tests.tg import as_user, make_init_data, TEST_BOT_TOKEN, reset_db, accept_terms

PAYLOAD = {"category": "marketplace", "title": "Диван в хорошем состоянии", "city": "Сеул",
           "price": "150 000 ₩", "description": "Самовывоз", "contact": "@seller"}
ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3, 5, 6):
            accept_terms(c, uid)
        yield c


def publish(client, user=1, title=None):
    body = {**PAYLOAD, **({"title": title} if title else {})}
    lid = client.post("/api/listings", json=body, headers=as_user(user)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


# ---- auth ----

def test_expired_init_data_rejected(client):
    old = make_init_data(1, auth_date=0)
    r = client.get("/api/me/listings", headers={"X-Telegram-Init-Data": old})
    assert r.status_code == 401


def test_missing_user_rejected(client):
    assert client.get("/api/favorites").status_code == 401


# ---- favorites ----

def test_favorite_toggle_and_list(client):
    lid = publish(client)
    assert client.post(f"/api/favorites/{lid}", headers=as_user(5)).json() == {"favorited": True}
    favs = client.get("/api/favorites", headers=as_user(5)).json()
    assert [x["id"] for x in favs] == [lid]
    assert client.get("/api/favorites", headers=as_user(6)).json() == []
    assert client.post(f"/api/favorites/{lid}", headers=as_user(5)).json() == {"favorited": False}
    assert client.get("/api/favorites", headers=as_user(5)).json() == []


def test_favorite_unknown_listing(client):
    assert client.post("/api/favorites/9999", headers=as_user(5)).status_code == 404


# ---- chats ----

def test_chat_between_buyer_and_seller(client):
    lid = publish(client, user=1)
    conv = client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]
    # Повторный запуск возвращает тот же чат
    assert client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"] == conv

    m = client.post(f"/api/conversations/{conv}/messages", json={"text": "Здравствуйте"}, headers=as_user(2))
    assert m.status_code == 201 and m.json()["mine"] is True

    seller_view = client.get(f"/api/conversations/{conv}/messages", headers=as_user(1)).json()
    assert seller_view[0]["text"] == "Здравствуйте"
    assert seller_view[0]["mine"] is False

    client.post(f"/api/conversations/{conv}/messages", json={"text": "Да, актуально"}, headers=as_user(1))
    buyer_list = client.get("/api/conversations", headers=as_user(2)).json()
    assert buyer_list[0]["last_text"] == "Да, актуально"
    assert buyer_list[0]["listing_title"] == PAYLOAD["title"]


def test_third_party_cannot_read_or_write_chat(client):
    lid = publish(client, user=1)
    conv = client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]
    assert client.get(f"/api/conversations/{conv}/messages", headers=as_user(3)).status_code == 404
    assert client.post(f"/api/conversations/{conv}/messages", json={"text": "hi"},
                       headers=as_user(3)).status_code == 404


def test_cannot_chat_with_own_listing_or_unapproved(client):
    pending = client.post("/api/listings", json=PAYLOAD, headers=as_user(1)).json()["id"]
    assert client.post(f"/api/listings/{pending}/conversation", headers=as_user(2)).status_code == 404
    lid = publish(client, user=1, title="Стол письменный")
    assert client.post(f"/api/listings/{lid}/conversation", headers=as_user(1)).status_code == 400


def test_message_validation(client):
    lid = publish(client, user=1)
    conv = client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]
    assert client.post(f"/api/conversations/{conv}/messages", json={"text": ""},
                       headers=as_user(2)).status_code == 422
    assert client.post(f"/api/conversations/{conv}/messages", json={"text": "x" * 2001},
                       headers=as_user(2)).status_code == 422
