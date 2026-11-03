import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import DatingProfile, Payment, User
from tests.tg import as_user, reset_db, accept_terms
from tests.test_dating import ADMIN, publish


@pytest.fixture()
def client(tmp_path, monkeypatch):
    assert engine.url.drivername == "sqlite"
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def test_services_list_and_balance_payment(client):
    publish(client, 1)
    r = client.get("/api/dating/services", headers=as_user(1)).json()
    assert r["balance"] == 0
    assert {o["kind"] for o in r["options"]} >= {"vip", "invisible", "viewers"}
    with SessionLocal() as db:
        db.get(User, "1").balance_stars = 500
        db.commit()
    inv = client.post("/api/dating/invoice", json={"service_code": "dating_vip", "use_balance": True}, headers=as_user(1))
    assert inv.status_code == 200 and inv.json()["paid"] is True
    with SessionLocal() as db:
        p = db.query(DatingProfile).one()
        assert p.vip_until is not None
        assert db.get(User, "1").balance_stars == 500 - 70


def test_grant_marks_and_is_not_refunded(client):
    publish(client, 1)
    r = client.post("/api/admin/grants", json={"tg_id": "1", "service_code": "dating_vip", "note": "тест", "promo": True}, headers=ADMIN)
    assert r.status_code == 201
    with SessionLocal() as db:
        pay = db.query(Payment).one()
        assert pay.status == "granted" and "По акции" in pay.note
        p = db.query(DatingProfile).one()
        assert p.vip_promo is True
    # no payment for a person without a profile
    assert client.post("/api/admin/grants", json={"tg_id": "3", "service_code": "dating_vip"}, headers=ADMIN).status_code == 400


def test_proportional_refund_goes_to_balance(client):
    publish(client, 1)
    with SessionLocal() as db:
        db.get(User, "1").balance_stars = 200
        db.commit()
    pid = client.post("/api/dating/invoice", json={"service_code": "dating_vip", "use_balance": True}, headers=as_user(1)).json()["payment_id"]
    r = client.post(f"/api/admin/payments/{pid}/refund?mode=balance&reason=test", headers=ADMIN)
    assert r.status_code == 200
    credited = r.json()["credited"]
    assert 60 <= credited <= 70
    with SessionLocal() as db:
        assert db.get(User, "1").balance_stars == 200 - 70 + credited
        assert db.query(DatingProfile).one().vip_until is None


def test_blocking_returns_unused_part(client):
    publish(client, 1)
    with SessionLocal() as db:
        db.get(User, "1").balance_stars = 100
        db.commit()
    client.post("/api/dating/invoice", json={"service_code": "dating_vip", "use_balance": True}, headers=as_user(1))
    from app.admin_ops import block_user
    with SessionLocal() as db:
        block_user(db, "t", "1", "нарушение правил")
        assert db.get(User, "1").balance_stars > 30


def test_bump_puts_the_profile_first_for_a_day_and_stats_count(client):
    p1, p2, p3 = publish(client, 1), publish(client, 2), publish(client, 3)
    feed = [x["id"] for x in client.get("/api/dating", headers=as_user(2)).json()]
    assert feed.index(p3) < feed.index(p1)                                   # newer first by default
    with SessionLocal() as db:
        db.get(User, "1").balance_stars = 100
        db.commit()
    r = client.post("/api/dating/invoice", json={"service_code": "dating_bump", "use_balance": True}, headers=as_user(1))
    assert r.status_code == 200 and r.json()["paid"] is True
    feed = [x["id"] for x in client.get("/api/dating", headers=as_user(2)).json()]
    assert feed[0] == p1
    assert client.get("/api/dating/me", headers=as_user(1)).json()["bumped_until"]
    client.get(f"/api/dating/{p1}", headers=as_user(2))                       # a view is recorded
    st = client.get("/api/dating/me/stats", headers=as_user(1)).json()
    assert st["views_7d"] == 1 and st["mutual"] == 0
    client.post(f"/api/dating/{p1}/like", headers=as_user(2))
    assert client.get("/api/dating/me/stats", headers=as_user(1)).json()["likes_to_me"] == 1
