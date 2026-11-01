import pytest
from fastapi.testclient import TestClient

from app.database import engine
from app.main import app
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def chat(client, title):
    lid = client.post("/api/listings", json={"category": "marketplace", "title": title, "city": "Сеул", "description": "ok", "contact": "", "price_amount": 1000}, headers=as_user(1)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    cid = client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": f"Вопрос про {title}"}, headers=as_user(2))
    return cid


def test_pinned_chat_goes_first_and_limits_hold(client):
    first, second = chat(client, "Велосипед"), chat(client, "Холодильник")
    assert [c["id"] for c in client.get("/api/conversations", headers=as_user(2)).json()] == [second, first]
    assert client.post(f"/api/conversations/{first}/pin", headers=as_user(2)).status_code == 200
    rows = client.get("/api/conversations", headers=as_user(2)).json()
    assert [c["id"] for c in rows] == [first, second] and rows[0]["pinned"] is True and rows[1]["pinned"] is False
    assert client.get(f"/api/conversations/{first}/info", headers=as_user(2)).json()["pinned"] is True
    assert client.post(f"/api/conversations/{first}/pin", headers=as_user(3)).status_code == 404            # not a chat of this person
    client.delete(f"/api/conversations/{first}/pin", headers=as_user(2))
    assert [c["id"] for c in client.get("/api/conversations", headers=as_user(2)).json()] == [second, first]


def test_own_quick_replies(client):
    r = client.post("/api/me/quick-replies", json={"text": "Адрес и время работы пришлю в чате"}, headers=as_user(1))
    assert r.status_code == 201
    assert client.post("/api/me/quick-replies", json={"text": "Адрес и время работы пришлю в чате"}, headers=as_user(1)).status_code == 409
    assert [x["text"] for x in client.get("/api/me/quick-replies", headers=as_user(1)).json()] == ["Адрес и время работы пришлю в чате"]
    assert client.get("/api/me/quick-replies", headers=as_user(2)).json() == []
    assert client.delete(f"/api/me/quick-replies/{r.json()['id']}", headers=as_user(2)).status_code == 404
    assert client.delete(f"/api/me/quick-replies/{r.json()['id']}", headers=as_user(1)).status_code == 200


def test_chat_info_tells_the_company_and_a_listing_link_message_is_plain_text(client):
    cid = chat(client, "Велосипед")
    info = client.get(f"/api/conversations/{cid}/info", headers=as_user(1)).json()
    assert info["company_id"] is None
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": "🔗 Объявление #1: Велосипед"}, headers=as_user(1))
    assert r.status_code in (200, 201)
