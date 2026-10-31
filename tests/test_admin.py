import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import Base, engine
from app.main import app
from app import bot as bot_module


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


def create(client, title="Работа на заводе"):
    r = client.post("/api/listings", json={
        "category": "job", "title": title, "city": "Асан",
        "price": "2 800 000 ₩", "description": "Смена 8-17", "contact": "@test",
        "author_telegram_id": "42",
    })
    assert r.status_code == 201
    return r.json()["id"]


def test_admin_page_served(client):
    r = client.get("/admin")
    assert r.status_code == 200
    assert "модерация" in r.text


def test_admin_api_requires_key(client):
    assert client.get("/api/admin/listings").status_code == 401
    assert client.get("/api/admin/listings", headers={"X-Admin-Key": "wrong"}).status_code == 401


def test_approve_publishes_listing(client):
    listing_id = create(client)
    headers = {"X-Admin-Key": settings.admin_api_key}
    pending = client.get("/api/admin/listings", headers=headers).json()
    assert any(x["id"] == listing_id for x in pending)

    r = client.post(f"/api/admin/listings/{listing_id}/approve", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    public = client.get("/api/listings").json()
    assert any(x["id"] == listing_id for x in public)


def test_reject_hides_listing(client):
    listing_id = create(client)
    headers = {"X-Admin-Key": settings.admin_api_key}
    client.post(f"/api/admin/listings/{listing_id}/reject", headers=headers)
    assert all(x["id"] != listing_id for x in client.get("/api/listings").json())


def test_moderate_twice_is_refused(client):
    listing_id = create(client)
    item, error = bot_module.moderate(listing_id, "approve")
    assert error == "" and item.status == "approved"
    item, error = bot_module.moderate(listing_id, "reject", "поздно")
    assert "уже" in error


def test_moderate_unknown_id(client):
    item, error = bot_module.moderate(999999, "approve")
    assert item is None and "не найдено" in error


def test_admin_ids_parsing(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", "111, 222,,abc,333")
    assert bot_module.admin_ids() == {111, 222, 333}
    assert bot_module.is_admin(222)
    assert not bot_module.is_admin(999)
    assert not bot_module.is_admin(None)
