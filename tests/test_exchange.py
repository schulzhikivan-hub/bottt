import pytest
from fastapi.testclient import TestClient

from app import payments, rules
from app.database import SessionLocal, engine
from app.main import app
from app.models import Payment, User
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client(monkeypatch):
    assert engine.url.drivername == "sqlite"
    reset_db()
    rules.reset_cache()
    monkeypatch.setattr(payments, "telegram_call", lambda method, payload: "https://t.me/invoice/x")
    with TestClient(app) as c:
        for uid in (1, 2):
            accept_terms(c, uid)
        yield c
    rules.reset_cache()


def listing(client):
    lid = client.post("/api/listings", json={"category": "job", "title": "Повар", "city": "Сеул", "description": "ok", "contact": ""}, headers=as_user(1)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def set_rate(client, per100, only):
    return client.put("/api/admin/exchange", json={"per_100_stars": per100, "balance_only": only}, headers=ADMIN)


def test_default_is_one_star_one_kh(client):
    assert client.get("/api/exchange").json()["kh_per_star"] == 1.0
    r = client.post("/api/me/balance/topup", json={"kh": 100}, headers=as_user(1)).json()
    assert r["stars"] == 100 and r["kh"] == 100


def test_rate_other_than_one_needs_balance_only_and_changes_the_top_up(client):
    assert set_rate(client, 200, False).status_code == 400                    # two prices for one service: refused
    assert set_rate(client, 200, True).status_code == 200                     # 1 star = 2 KH
    assert client.get("/api/exchange").json()["kh_per_star"] == 2.0
    r = client.post("/api/me/balance/topup", json={"kh": 101}, headers=as_user(1)).json()
    assert r["stars"] == 51 and r["kh"] == 101                                # rounded up in the owner's favour
    with SessionLocal() as db:
        pay = db.query(Payment).one()
        payments.process_successful_payment(db, "1", pay.payload, "c1", "XTR", 51)
        assert db.get(User, "1").balance_stars == 101


def test_services_are_paid_only_from_the_kh_balance_when_the_switch_is_on(client):
    lid = listing(client)
    assert set_rate(client, 100, True).status_code == 200
    r = client.post("/api/payments/invoice", json={"listing_id": lid, "service_code": "job_bump"}, headers=as_user(1))
    assert r.status_code == 400 and "Недостаточно KH" in r.json()["detail"]
    with SessionLocal() as db:
        db.get(User, "1").balance_stars = 500
        db.commit()
    r = client.post("/api/payments/invoice", json={"listing_id": lid, "service_code": "job_bump"}, headers=as_user(1))
    assert r.status_code in (200, 201), r.text
    with SessionLocal() as db:
        assert db.get(User, "1").balance_stars == 470                         # 30 KH taken from the balance
        assert db.query(Payment).filter_by(service_code="job_bump").one().stars == 0   # nothing was charged in Stars


def test_admin_sees_the_exchange_and_circulation(client):
    with SessionLocal() as db:
        db.get(User, "1").balance_stars = 40
        db.commit()
    x = client.get("/api/admin/exchange", headers=ADMIN).json()
    assert x["in_circulation"]["people"] == 40 and x["per_100_stars"] == 100


def test_kh_report_by_day_and_csv(client):
    client.post("/api/me/balance/topup", json={"kh": 100}, headers=as_user(1))
    with SessionLocal() as db:
        pay = db.query(Payment).one()
        payments.process_successful_payment(db, "1", pay.payload, "c-rep", "XTR", 100)
    rep = client.get("/api/admin/exchange/report?days=14", headers=ADMIN).json()
    assert len(rep["days"]) == 14 and rep["total"]["stars_in"] == 100 and rep["total"]["kh_bought"] == 100 and rep["total"]["kh_spent"] == 0
    csv = client.get("/api/admin/exchange/report.csv", headers=ADMIN)
    assert csv.status_code == 200 and "Куплено KH" in csv.text and csv.headers["content-type"].startswith("text/csv")
