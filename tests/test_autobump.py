import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import payments
from app.database import SessionLocal, engine
from app.main import app
from app.models import Placement, User
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client(monkeypatch):
    assert engine.url.drivername == "sqlite"
    reset_db()
    monkeypatch.setattr(payments, "telegram_call", lambda m, p: "https://t.me/invoice/x")
    with TestClient(app) as c:
        accept_terms(c, 1)
        yield c


def test_autobump_lifts_the_listing_once_a_day_and_is_not_sold_as_a_placement(client):
    lid = client.post("/api/listings", json={"category": "job", "title": "Повар", "city": "Сеул", "description": "ok", "contact": ""}, headers=as_user(1)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    with SessionLocal() as db:
        db.get(User, "1").balance_stars = 500
        db.commit()
    r = client.post("/api/payments/invoice", json={"listing_id": lid, "service_code": "job_autobump", "use_balance": True}, headers=as_user(1))
    assert r.status_code == 200 and r.json()["paid"] is True
    with SessionLocal() as db:
        assert db.get(User, "1").balance_stars == 380                     # 120 KH
        start = payments.now()
        assert payments.run_autobump(db, start) == 1                      # the first bump at once
        assert payments.run_autobump(db, start + dt.timedelta(hours=5)) == 0
        assert payments.run_autobump(db, start + dt.timedelta(hours=25)) == 1
        bumps = db.query(Placement).filter_by(kind="bump").all()
        assert len(bumps) == 2 and all(b.payment_id is None for b in bumps)
    assert client.get("/api/catalog?category=job").status_code == 200       # the feed does not choke on the new kind
    assert client.get(f"/api/listings/{lid}").json()["promo"] in {"bump", None}
    assert client.get("/api/me/listings", headers=as_user(1)).status_code == 200
