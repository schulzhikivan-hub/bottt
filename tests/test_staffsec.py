import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import admin_auth
from app.admin_auth import _hash
from app.database import SessionLocal, engine
from app.main import app
from app.models import Admin, AdminSession
from app.payments import now
from tests.tg import reset_db

ADMIN = {"X-Admin-Session": "test-session-token"}
MOD = {"X-Admin-Session": "mod-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        with SessionLocal() as db:
            db.add(Admin(tg_id="555", role="moderator", active=True, created_at=now()))
            db.add(AdminSession(token_hash=_hash("mod-token"), tg_id="555", expires_at=now() + dt.timedelta(hours=1)))
            db.commit()
        yield c


def test_temporary_rights_come_and_go_by_themselves(client):
    assert client.get("/api/admin/stats", headers=MOD).status_code == 403                    # a moderator has no statistics
    r = client.post("/api/admin/admins/555/elevate", json={"role": "admin", "days": 3, "reason": "замена на отпуск"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["role"] == "admin"
    assert client.get("/api/admin/stats", headers=MOD).status_code == 200                    # now it works
    assert client.post("/api/admin/admins/555/elevate", json={"role": "moderator", "days": 3, "reason": "это не повышение"}, headers=ADMIN).status_code == 400
    assert client.post("/api/admin/admins/555/elevate", json={"role": "admin", "days": 30, "reason": "слишком долго"}, headers=ADMIN).status_code == 422
    with SessionLocal() as db:
        a = db.get(Admin, "555")
        a.elevated_until = now() - dt.timedelta(minutes=1)
        db.commit()
    assert client.get("/api/admin/stats", headers=MOD).status_code == 403                    # it ran out
    with SessionLocal() as db:
        a = db.get(Admin, "555")
        assert a.role == "moderator" and a.base_role is None and a.elevated_until is None


def test_force_logout_closes_every_sign_in(client):
    assert client.get("/api/admin/counters", headers=MOD).status_code == 200
    assert client.post("/api/admin/admins/555/logout", headers=ADMIN).json() == {"closed": 1}
    assert client.get("/api/admin/counters", headers=MOD).status_code == 401
    assert client.post("/api/admin/admins/555/logout", headers=MOD).status_code in (401, 403)


def test_super_admin_can_be_limited_to_trusted_addresses_without_locking_himself_out(client):
    info = client.get("/api/admin/security/ips", headers=ADMIN).json()
    mine = info["your_ip"]
    assert client.put("/api/admin/security/ips", json={"ips": "10.9.9.9"}, headers=ADMIN).status_code == 400          # own address missing
    assert client.put("/api/admin/security/ips", json={"ips": f"10.9.9.9, {mine}"}, headers=ADMIN).status_code == 200
    assert client.get("/api/admin/stats", headers=ADMIN).status_code == 200
    assert client.get("/api/admin/stats", headers={**ADMIN, "X-Forwarded-For": "8.8.8.8"}).status_code == 403          # another address
    assert client.get("/api/admin/counters", headers=MOD).status_code == 200                                           # others are not affected
    assert client.put("/api/admin/security/ips", json={"ips": ""}, headers=ADMIN).status_code == 200                   # switch off


def test_accountant_sees_money_but_not_moderation_or_people(client):
    with SessionLocal() as db:
        db.add(Admin(tg_id="556", role="accountant", active=True, created_at=now()))
        db.add(AdminSession(token_hash=_hash("acc-token"), tg_id="556", expires_at=now() + dt.timedelta(hours=1)))
        db.commit()
    acc = {"X-Admin-Session": "acc-token"}
    for ok in ("/api/admin/payments", "/api/admin/refunds", "/api/admin/reports/money", "/api/admin/vip/history"):
        assert client.get(ok, headers=acc).status_code == 200, ok
    for no in ("/api/admin/listings", "/api/admin/users-list", "/api/admin/stats", "/api/admin/rules", "/api/admin/security/pii-views"):
        assert client.get(no, headers=acc).status_code == 403, no
    assert client.put("/api/admin/admins/556", json={"role": "accountant", "active": True}, headers=ADMIN).status_code == 200
