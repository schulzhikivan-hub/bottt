import pytest
from fastapi.testclient import TestClient

from app import adminplus, guard
from app.database import SessionLocal, engine
from app.main import app
from tests.tg import reset_db

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client(monkeypatch):
    assert engine.url.drivername == "sqlite"
    reset_db()
    told = []
    monkeypatch.setattr(guard, "notify_user", lambda db, tg, text, kind: told.append((tg, text)))
    with TestClient(app) as c:
        c.told = told
        yield c


def test_staff_chooses_what_to_hear_about(client):
    me = client.get("/api/admin/my-notifications", headers=ADMIN).json()
    assert me["mode"] == "all" and any(k["key"] == "report" for k in me["kinds"])
    with SessionLocal() as db:
        assert adminplus.notify_staff(db, "Новая жалоба", "report", ("super_admin",)) >= 1
        assert adminplus.notify_staff(db, "Анкета", "dating", ("super_admin",)) >= 1
    client.told.clear()
    assert client.put("/api/admin/my-notifications", json={"mode": "important", "muted": ["report", "bogus"]}, headers=ADMIN).json() == {"mode": "important", "muted": ["report"]}
    with SessionLocal() as db:
        adminplus.notify_staff(db, "Новая жалоба", "report", ("super_admin",))       # muted
        adminplus.notify_staff(db, "Анкета", "dating", ("super_admin",))             # routine: below «important»
        adminplus.notify_staff(db, "Возврат", "refund", ("super_admin",))            # important: still comes
        adminplus.notify_staff(db, "Новый адрес", "newip", ("super_admin",))         # critical: comes
    assert [t for _, t in client.told] == ["Возврат", "Новый адрес"]
    assert client.put("/api/admin/my-notifications", json={"mode": "everything"}, headers=ADMIN).status_code == 400


def test_reindex_keeps_a_version_and_can_be_rolled_back(client):
    from app.models import Listing
    from tests.tg import as_user, accept_terms
    accept_terms(client, 1)
    lid = client.post("/api/listings", json={"category": "job", "title": "Повар", "city": "Сеул", "description": "кухня", "contact": "",
                                             "details": {"company": "Кафе Сеул", "position": "Шеф"}}, headers=as_user(1)).json()["id"]
    with SessionLocal() as db:
        from sqlalchemy import update
        db.execute(update(Listing).where(Listing.id == lid).values(search_text="устарело"))      # a stale text, as if the rule had changed
        db.commit()
    assert client.get("/api/admin/search/index-versions", headers=ADMIN).json()["out_of_date"] == 1
    r = client.post("/api/admin/search/reindex", headers=ADMIN).json()
    assert r["updated"] == 1 and r["version"]
    with SessionLocal() as db:
        assert "шеф" in db.get(Listing, lid).search_text and "кафе сеул" in db.get(Listing, lid).search_text
    assert client.get("/api/admin/search/index-versions", headers=ADMIN).json()["out_of_date"] == 0
    assert client.post("/api/admin/search/reindex", headers=ADMIN).json() == {"updated": 0, "version": None}     # nothing to do: no empty version
    assert client.post(f"/api/admin/search/index-versions/{r['version']}/rollback", headers=ADMIN).json() == {"restored": 1}
    with SessionLocal() as db:
        assert db.get(Listing, lid).search_text == "устарело"
    assert client.post(f"/api/admin/search/index-versions/{r['version']}/rollback", headers=ADMIN).status_code == 409
