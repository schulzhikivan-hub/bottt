import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app
from tests.tg import as_user, make_init_data, reset_db, accept_terms


@pytest.fixture()
def client():
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 777):
            accept_terms(c, uid)
        yield c


PAYLOAD = {
    "category": "job",
    "title": "Работа на заводе",
    "city": "Асан",
    "price": "2 800 000 ₩",
    "description": "Смена 8-17",
    "contact": "@test",
}


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_create_listing_pending_then_not_public(client):
    created = client.post("/api/listings", json=PAYLOAD, headers=as_user(1))
    assert created.status_code == 201
    assert created.json()["status"] == "pending"
    assert all(x["id"] != created.json()["id"] for x in client.get("/api/listings").json())


def test_create_requires_telegram_auth(client):
    assert client.post("/api/listings", json=PAYLOAD).status_code == 401


def test_forged_init_data_rejected(client):
    forged = make_init_data(1, bot_token="999:WRONG")
    r = client.post("/api/listings", json=PAYLOAD, headers={"X-Telegram-Init-Data": forged})
    assert r.status_code == 401


def test_author_cannot_be_spoofed_in_body(client):
    body = {**PAYLOAD, "author_telegram_id": "777"}
    created = client.post("/api/listings", json=body, headers=as_user(1))
    assert created.status_code == 201
    mine = client.get("/api/me/listings", headers=as_user(1)).json()
    assert any(x["id"] == created.json()["id"] for x in mine)
    assert client.get("/api/me/listings", headers=as_user(777)).json() == []


def test_category_limit_per_author(client):
    for i in range(5):
        assert client.post("/api/listings", json={**PAYLOAD, "title": f"Работа {i}"},
                           headers=as_user(1)).status_code == 201
    r = client.post("/api/listings", json=PAYLOAD, headers=as_user(1))
    assert r.status_code == 409
    # Другой автор не ограничен
    assert client.post("/api/listings", json=PAYLOAD, headers=as_user(2)).status_code == 201


def test_unknown_category_rejected(client):
    r = client.post("/api/listings", json={**PAYLOAD, "category": "cars"}, headers=as_user(1))
    assert r.status_code == 400
