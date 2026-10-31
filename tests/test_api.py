import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_create_listing_pending_then_not_public(client):
    payload = {
        "category": "job",
        "title": "Работа на заводе",
        "city": "Асан",
        "price": "2 800 000 ₩",
        "description": "Смена 8-17",
        "contact": "@test",
    }
    created = client.post("/api/listings", json=payload)
    assert created.status_code == 201
    assert created.json()["status"] == "pending"
    assert all(x["id"] != created.json()["id"] for x in client.get("/api/listings").json())
