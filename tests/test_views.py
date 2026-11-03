import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.admin_auth import _hash
from app.database import SessionLocal, engine
from app.main import app
from app.models import Admin, AdminSession
from app.payments import now
from tests.tg import reset_db

ADMIN = {"X-Admin-Session": "test-session-token"}
SUPPORT = {"X-Admin-Session": "support-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        with SessionLocal() as db:
            db.add(Admin(tg_id="888", role="support", active=True, created_at=now()))
            db.add(AdminSession(token_hash=_hash("support-token"), tg_id="888", expires_at=now() + dt.timedelta(hours=1)))
            db.commit()
        yield c


def test_saved_views_are_private_until_shared_and_limited(client):
    state = {"filter": {"flt": "blocked"}, "cols": ["name", "status"]}
    v = client.post("/api/admin/views", json={"tab": "users", "name": "Заблокированные", "state": state}, headers=ADMIN).json()
    assert v["mine"] is True and client.get("/api/admin/views?tab=users", headers=SUPPORT).json() == {"mine": [], "shared": []}       # private
    assert client.post("/api/admin/views", json={"tab": "users", "name": "заблокированные", "state": {}}, headers=ADMIN).status_code == 409   # same name
    client.put(f"/api/admin/views/{v['id']}/share", json={"shared": True}, headers=ADMIN)
    seen = client.get("/api/admin/views?tab=users", headers=SUPPORT).json()["shared"]
    assert [x["name"] for x in seen] == ["Заблокированные"] and seen[0]["state"] == state and seen[0]["mine"] is False
    assert client.put(f"/api/admin/views/{v['id']}/share", json={"shared": False}, headers=SUPPORT).status_code == 404           # not theirs
    assert client.delete(f"/api/admin/views/{v['id']}", headers=SUPPORT).status_code == 404
    assert client.post("/api/admin/views", json={"tab": "nope", "name": "ещё", "state": {}}, headers=ADMIN).status_code == 400
    for i in range(19):
        assert client.post("/api/admin/views", json={"tab": "users", "name": f"Вид {i}", "state": {}}, headers=ADMIN).status_code == 201
    assert client.post("/api/admin/views", json={"tab": "users", "name": "лишний", "state": {}}, headers=ADMIN).status_code == 409   # 20 is the limit
    assert client.delete(f"/api/admin/views/{v['id']}", headers=ADMIN).json() == {"deleted": True}


def test_user_list_has_the_balance_column_data(client):
    r = client.get("/api/admin/users-list", headers=ADMIN).json()
    assert r["total"] == 0 and r["items"] == []
