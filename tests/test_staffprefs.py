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
