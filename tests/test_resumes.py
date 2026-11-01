import datetime as dt

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Listing, Resume
from app.payments import now
from tests.tg import accept_terms, as_user, reset_db, TEST_ADMIN_SESSION

ADMIN = {"X-Admin-Session": TEST_ADMIN_SESSION}
CV = {"position": "Сварщик", "city": "Асан", "visa": "E-9", "experience": "3 года на заводе", "about": "Ответственный", "salary_from": 2500000}


def world():
    reset_db()
    c = TestClient(app)
    for u in (1, 2, 3):
        accept_terms(c, u)
    with SessionLocal() as db:
        job = Listing(author_telegram_id="1", category="job", title="Сварщик на завод", description="x" * 40, city="Асан",
                      status="approved", contact="c", expires_at=now() + dt.timedelta(days=5))
        db.add(job); db.commit()
        return c, job.id


def test_resume_is_hidden_until_approved_and_only_employers_search():
    c, job = world()
    assert c.put("/api/me/resume", json=CV, headers=as_user(2)).json()["status"] == "pending"
    assert c.get("/api/resumes", headers=as_user(1)).json() == []
    assert c.get("/api/resumes", headers=as_user(3)).status_code == 403      # 3 has no vacancy
    assert c.post("/api/admin/resumes/2/reject", headers=ADMIN).status_code == 400   # reason required
    assert c.post("/api/admin/resumes/2/approve", headers=ADMIN).json()["status"] == "approved"
    found = c.get("/api/resumes?q=сварщ", headers=as_user(1)).json()
    assert len(found) == 1 and "owner" not in found[0] and found[0]["id"] != "2"
    assert c.get("/api/resumes?visa=F-6", headers=as_user(1)).json() == []


def test_edit_goes_back_to_moderation_and_contacts_are_refused():
    c, job = world()
    c.put("/api/me/resume", json=CV, headers=as_user(2))
    c.post("/api/admin/resumes/2/approve", headers=ADMIN)
    c.put("/api/me/resume", json={**CV, "about": "Новый текст"}, headers=as_user(2))
    assert c.get("/api/me/resume", headers=as_user(2)).json()["status"] == "pending"
    bad = c.put("/api/me/resume", json={**CV, "about": "звоните +82 10 1234 5678"}, headers=as_user(2))
    assert bad.status_code == 400


def test_invite_opens_chat_about_own_vacancy():
    c, job = world()
    c.put("/api/me/resume", json=CV, headers=as_user(2))
    c.post("/api/admin/resumes/2/approve", headers=ADMIN)
    rid = c.get("/api/resumes", headers=as_user(1)).json()[0]["id"]
    assert c.post(f"/api/resumes/{rid}/invite", json={"listing_id": job}, headers=as_user(3)).status_code == 400  # not their vacancy
    ok = c.post(f"/api/resumes/{rid}/invite", json={"listing_id": job}, headers=as_user(1))
    assert ok.status_code == 200
    msgs = c.get(f"/api/conversations/{ok.json()['conversation_id']}/messages", headers=as_user(2)).json()
    assert msgs and "вакансию" in msgs[0]["text"]


def test_block_hides_resume_and_delete_removes_it():
    c, job = world()
    c.put("/api/me/resume", json=CV, headers=as_user(2))
    c.post("/api/admin/resumes/2/approve", headers=ADMIN)
    assert c.post("/api/admin/users/2/block", json={"reason": "спам"}, headers=ADMIN).status_code == 200
    with SessionLocal() as db:
        assert db.get(Resume, "2").status == "hidden"
    c.post("/api/admin/users/2/unblock", json={"reason": "ошибка"}, headers=ADMIN)
    with SessionLocal() as db:
        assert db.get(Resume, "2").status == "approved"
    assert c.get("/api/me/export", headers=as_user(2)).json()["resume"]["position"] == "Сварщик"
    c.post("/api/me/delete", headers=as_user(2))
    with SessionLocal() as db:
        assert db.get(Resume, "2") is None
