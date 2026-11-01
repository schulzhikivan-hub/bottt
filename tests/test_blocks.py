import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import adminplus
from app.database import SessionLocal, engine
from app.main import app
from app.models import Admin, Listing, Ticket, User
from app.payments import now
from tests.tg import as_user, reset_db, accept_terms
from tests.test_catalog import make

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2):
            accept_terms(c, uid)
        yield c


def test_presets_cascade_and_term(client):
    lid = make(client, user=1)
    cfg = client.get("/api/admin/block-presets", headers=ADMIN).json()
    assert {p["key"] for p in cfg["presets"]} == {"spam", "fraud", "abuse", "rules"} and cfg["can_permanent"] is True
    r = client.post("/api/admin/users/1/block-preset", json={"preset": "spam"}, headers=ADMIN).json()
    assert r["blocked"] is True and r["until"]
    with SessionLocal() as db:
        assert db.get(Listing, lid).status == "hidden" and db.get(User, "1").blocked is True
    client.post("/api/admin/users/1/unblock", json={"reason": "ошибка"}, headers=ADMIN)
    with SessionLocal() as db:
        assert db.get(Listing, lid).status == "approved"
    # a gentle preset leaves the listings alone
    client.post("/api/admin/users/1/block-preset", json={"preset": "abuse"}, headers=ADMIN)
    with SessionLocal() as db:
        assert db.get(Listing, lid).status == "approved"
    assert client.post("/api/admin/users/2/block-preset", json={}, headers=ADMIN).status_code == 400                  # no term, no preset
    assert client.post("/api/admin/users/2/block-preset", json={"preset": "nope"}, headers=ADMIN).status_code == 400


def test_support_can_not_block_forever(client):
    with SessionLocal() as db:
        from app.admin_auth import _hash
        from app.models import AdminSession
        db.add(Admin(tg_id="888", role="support", active=True, created_at=now()))
        db.add(AdminSession(token_hash=_hash("support-token"), tg_id="888", expires_at=now() + dt.timedelta(hours=1)))
        db.commit()
    s = {"X-Admin-Session": "support-token"}
    assert client.post("/api/admin/users/1/block-preset", json={"preset": "fraud"}, headers=s).status_code == 403
    assert client.post("/api/admin/users/1/block-preset", json={"days": 3, "reason": "спам"}, headers=s).status_code == 200


def test_appeal_keeps_block_until_answered_or_closes_it(client):
    from app import rules
    client.post("/api/admin/users/1/block-preset", json={"days": 1, "reason": "тест"}, headers=ADMIN)
    st = client.get("/api/me/block-status", headers=as_user(1)).json()
    assert st["blocked"] is True and st["appeal_open"] is False
    assert client.post("/api/me/block-appeal", json={"text": "я не нарушал, прошу пересмотреть"}, headers=as_user(1)).status_code == 201
    assert client.post("/api/me/block-appeal", json={"text": "ещё раз то же самое тут"}, headers=as_user(1)).status_code == 409
    with SessionLocal() as db:
        db.get(User, "1").blocked_until = now() - dt.timedelta(minutes=1)
        db.commit()
        assert adminplus.unblock_expired(db) == 0                          # waits for the answer
        assert db.get(User, "1").blocked is True
    client.put("/api/admin/rules/blocks.extend_for_appeal", json={"value": False}, headers=ADMIN)
    rules.reset_cache()
    with SessionLocal() as db:
        assert adminplus.unblock_expired(db) == 1
        assert db.get(User, "1").blocked is False
        assert db.query(Ticket).one().status == "closed"                   # the appeal is closed: the block is over


def test_a_person_who_is_not_blocked_can_not_appeal(client):
    assert client.post("/api/me/block-appeal", json={"text": "оспариваю то, чего нет"}, headers=as_user(2)).status_code == 409
