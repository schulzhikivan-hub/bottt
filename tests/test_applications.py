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


def vacancy(client, category="job", publish=True):
    r = client.post("/api/listings", json={"category": category, "title": "Повар", "city": "Сеул", "description": "ok"},
                    headers=as_user(1))
    lid = r.json()["id"]
    if publish:
        client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def test_apply_and_employer_sees_it(client):
    lid = vacancy(client)
    client.put("/api/me/settings", json={"visa": "E-9"}, headers=as_user(2))
    r = client.post(f"/api/listings/{lid}/apply", json={"message": "Есть опыт", "contact": "@ivan"}, headers=as_user(2))
    assert r.status_code == 201
    assert client.post(f"/api/listings/{lid}/apply", json={"contact": "@ivan"}, headers=as_user(2)).status_code == 409
    mine = client.get("/api/me/listings", headers=as_user(1)).json()
    assert mine[0]["applications"] == {"total": 1, "new": 1}
    rows = client.get(f"/api/listings/{lid}/applications", headers=as_user(1)).json()
    assert rows[0]["contact"] == "@ivan" and rows[0]["visa"] == "E-9" and "applicant_tg_id" not in rows[0]
    assert client.get("/api/me/listings", headers=as_user(1)).json()[0]["applications"]["new"] == 0  # seen


def test_status_and_my_applications(client):
    lid = vacancy(client)
    client.post(f"/api/listings/{lid}/apply", json={"contact": "+82 10 1234 5678"}, headers=as_user(2))
    app_id = client.get(f"/api/listings/{lid}/applications", headers=as_user(1)).json()[0]["id"]
    assert client.put(f"/api/applications/{app_id}", json={"status": "invited"}, headers=as_user(3)).status_code == 404
    assert client.put(f"/api/applications/{app_id}", json={"status": "invited"}, headers=as_user(1)).status_code == 200
    mine = client.get("/api/me/applications", headers=as_user(2)).json()
    assert mine[0]["status"] == "invited" and mine[0]["title"] == "Повар"


def test_rules(client):
    lid = vacancy(client)
    assert client.post(f"/api/listings/{lid}/apply", json={"contact": "@me12"}, headers=as_user(1)).status_code == 400
    flat = vacancy(client, category="housing")
    assert client.post(f"/api/listings/{flat}/apply", json={"contact": "@abc"}, headers=as_user(2)).status_code == 404
    draft = vacancy(client, publish=False)
    assert client.post(f"/api/listings/{draft}/apply", json={"contact": "@abc"}, headers=as_user(2)).status_code == 404
    assert client.get(f"/api/listings/{lid}/applications", headers=as_user(2)).status_code == 404
    assert client.post(f"/api/listings/{lid}/apply", json={"contact": ""}, headers=as_user(2)).status_code == 422


def test_visa_is_private_and_validated(client):
    assert client.put("/api/me/settings", json={"visa": "XX"}, headers=as_user(2)).status_code == 422
    client.put("/api/me/settings", json={"visa": "F-4"}, headers=as_user(2))
    assert client.get("/api/me", headers=as_user(2)).json()["visa"] == "F-4"
    assert client.get("/api/me", headers=as_user(2)).json()["city"]  # city untouched


def test_author_block_has_no_telegram_id(client):
    lid = vacancy(client)
    author = client.get(f"/api/listings/{lid}").json()["author"]
    assert author["verified"] is False and author["active"] == 1 and "tg_id" not in author
