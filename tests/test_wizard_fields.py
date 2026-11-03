import pytest
from fastapi.testclient import TestClient

from app.database import engine
from app.main import app
from tests.tg import as_user, reset_db, accept_terms


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        accept_terms(c, 1)
        yield c


def test_car_details_are_kept_and_validated(client):
    body = {"category": "marketplace", "title": "Хендай Соната", "city": "Сеул", "goods_category": "auto",
            "details": {"model": "Sonata", "year": 2018, "mileage_km": 85000}}
    r = client.post("/api/listings", json=body, headers=as_user(1))
    assert r.status_code == 201 and r.json()["details"]["year"] == 2018
    body["details"]["year"] = 1800
    assert client.post("/api/listings", json=body, headers=as_user(1)).status_code == 422


def test_service_details_and_no_photo_needed(client, monkeypatch):
    from app import lifecycle
    monkeypatch.setattr(lifecycle.settings, "require_photos", True)
    body = {"category": "marketplace", "title": "Перевод документов", "city": "Сеул", "goods_category": "services",
            "details": {"service_area": "Сеул и Кёнги", "experience_years": 5}}
    lid = client.post("/api/listings", json=body, headers=as_user(1)).json()["id"]
    assert client.post(f"/api/admin/listings/{lid}/approve", headers={"X-Admin-Session": "test-session-token"}).status_code == 200
    goods = dict(body, title="Диван", goods_category="furniture")
    gid = client.post("/api/listings", json=goods, headers=as_user(1)).json()["id"]
    assert client.post(f"/api/admin/listings/{gid}/approve", headers={"X-Admin-Session": "test-session-token"}).status_code == 400
