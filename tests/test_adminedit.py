import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal, engine
from app.main import app
from app.models import AuditLog, Resume, Review
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2):
            accept_terms(c, uid)
        yield c


def test_company_is_edited_by_staff_with_journal(client):
    cid = client.post("/api/company", json={"name": "Кафе Сеул", "reg_no": "123-45-67890", "description": "x", "city": "Сеул"}, headers=as_user(1)).json()["id"]
    r = client.put(f"/api/admin/companies/{cid}/edit", json={"name": "Кафе Пусан", "city": "Пусан", "reason": "опечатка"}, headers=ADMIN)
    assert r.status_code == 200 and set(r.json()["changed"]) == {"name", "city"}
    row = next(x for x in client.get("/api/admin/companies?status=all", headers=ADMIN).json() if x["id"] == cid)
    assert row["name"] == "Кафе Пусан" and row["city"] == "Пусан"
    with SessionLocal() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "company.edit").count() == 1
    assert client.put("/api/admin/companies/999/edit", json={}, headers=ADMIN).status_code == 404


def test_resume_is_listed_edited_and_deleted_by_staff(client):
    assert client.put("/api/me/resume", json={"position": "Повар", "city": "Сеул", "about": "ok"}, headers=as_user(1)).status_code in (200, 201)
    rows = client.get("/api/admin/resumes-all?status=all", headers=ADMIN).json()
    assert len(rows) == 1 and rows[0]["position"] == "Повар"
    owner = rows[0]["owner"]
    r = client.put(f"/api/admin/resumes/{owner}/edit", json={"position": "Старший повар", "about": "Без телефона", "reason": "убрали контакт"}, headers=ADMIN)
    assert r.status_code == 200 and "position" in r.json()["changed"]
    with SessionLocal() as db:
        res = db.get(Resume, owner)
        assert res.position == "Старший повар" and "старший" in res.search_text
    assert client.get("/api/admin/resumes-all?q=старший", headers=ADMIN).json()
    assert client.delete(f"/api/admin/resumes/{owner}?reason=дубль", headers=ADMIN).status_code == 200
    assert client.get("/api/admin/resumes-all", headers=ADMIN).json() == []


def test_review_text_is_edited_and_review_deleted(client):
    with SessionLocal() as db:
        db.add(Review(listing_id=1, target_tg_id="1", reviewer_tg_id="2", rating=5, text="звоните 010-1111-2222"))
        db.commit()
        rid = db.query(Review).first().id
    assert client.put(f"/api/admin/reviews/{rid}/edit", json={"text": "Хороший работодатель"}, headers=ADMIN).json()["changed"] == ["text"]
    with SessionLocal() as db:
        assert db.get(Review, rid).text == "Хороший работодатель" and db.get(Review, rid).rating == 5
    assert client.delete(f"/api/admin/reviews/{rid}", headers=ADMIN).status_code == 200
    with SessionLocal() as db:
        assert db.get(Review, rid) is None
