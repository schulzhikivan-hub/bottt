import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func

from app import payments
from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import Listing, Payment, Placement, Pricing
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}
CALLS: list[tuple[str, dict]] = []


@pytest.fixture()
def client(monkeypatch):
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    reset_db()
    CALLS.clear()

    def fake_telegram(method, payload):
        CALLS.append((method, payload))
        if method == "createInvoiceLink":
            return f"https://t.me/invoice/{payload['payload'][:8]}"
        if method == "refundStarPayment":
            return True
        raise AssertionError(method)

    monkeypatch.setattr(payments, "telegram_call", fake_telegram)
    with TestClient(app) as c:
        accept_terms(c, 1)
        accept_terms(c, 2)
        yield c


def publish(client, user=1, category="job", title="Работа на заводе"):
    lid = client.post("/api/listings", json={
        "category": category, "title": title, "city": "Асан", "price": "2 800 000 ₩",
        "description": "Смена", "contact": "@test"}, headers=as_user(user)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def invoice(client, user, listing_id, code):
    return client.post("/api/payments/invoice", json={"listing_id": listing_id, "service_code": code},
                       headers=as_user(user))


def pay(client, user, payload_row_id, charge="charge-1"):
    """Simulate the bot receiving successful_payment for a stored invoice."""
    with SessionLocal() as db:
        p = db.get(Payment, payload_row_id)
        return payments.process_successful_payment(db, str(user), p.payload, charge, "XTR", p.stars)


def stored_payment(payment_id):
    with SessionLocal() as db:
        return db.get(Payment, payment_id)


def test_price_list_matches_tz2(client):
    services = {s["code"]: s["stars"] for s in client.get("/api/services").json()}
    assert services["job_bump"] == 30
    assert services["job_vip"] == 100
    assert services["job_pin"] == 70
    assert services["housing_bump"] == 20
    assert services["housing_vip"] == 70
    assert services["marketplace_vip"] == 50


def test_invoice_uses_xtr_and_no_provider(client):
    lid = publish(client, user=1)
    r = invoice(client, 1, lid, "job_vip")
    assert r.status_code == 200
    method, payload = CALLS[-1]
    assert method == "createInvoiceLink"
    assert payload["currency"] == "XTR"
    assert payload["provider_token"] == ""
    assert payload["prices"][0]["amount"] == 100


def test_invoice_requires_consent(client):
    lid = publish(client, user=1)
    r = client.post("/api/payments/invoice", json={"listing_id": lid, "service_code": "job_vip"},
                    headers=as_user(3))
    assert r.status_code == 403


def test_cannot_buy_for_someone_elses_listing(client):
    lid = publish(client, user=1)
    assert invoice(client, 2, lid, "job_vip").status_code == 400


def test_wrong_category_rejected(client):
    lid = publish(client, user=1)
    assert invoice(client, 1, lid, "housing_vip").status_code == 400


def test_pre_checkout_checks_amount_and_owner(client):
    lid = publish(client, user=1)
    pid = invoice(client, 1, lid, "job_vip").json()["payment_id"]
    p = stored_payment(pid)
    with SessionLocal() as db:
        assert payments.check_pre_checkout(db, p.payload, "1", "XTR", 100) is None
        assert payments.check_pre_checkout(db, p.payload, "1", "XTR", 99) is not None
        assert payments.check_pre_checkout(db, p.payload, "1", "USD", 100) is not None
        assert payments.check_pre_checkout(db, p.payload, "2", "XTR", 100) is not None
        assert payments.check_pre_checkout(db, "unknown", "1", "XTR", 100) is not None


def test_successful_payment_activates_once(client):
    lid = publish(client, user=1)
    pid = invoice(client, 1, lid, "job_vip").json()["payment_id"]
    first = pay(client, 1, pid, charge="charge-A")
    second = pay(client, 1, pid, charge="charge-A")  # repeated notification
    assert first.id == second.id
    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(Placement))
    assert count == 1
    assert stored_payment(pid).status == "paid"


def test_paid_vip_moves_listing_to_top(client):
    old = publish(client, user=1, title="Старое объявление")
    new = publish(client, user=1, title="Новое объявление")
    assert client.get("/api/listings").json()[0]["id"] == new
    pid = invoice(client, 1, old, "job_vip").json()["payment_id"]
    pay(client, 1, pid)
    assert client.get("/api/listings").json()[0]["id"] == old


def test_bump_cooldown_24h(client):
    lid = publish(client, user=1)
    pid = invoice(client, 1, lid, "job_bump").json()["payment_id"]
    pay(client, 1, pid, charge="bump-1")
    r = invoice(client, 1, lid, "job_bump")
    assert r.status_code == 400
    assert "24 часа" in r.json()["detail"]


def test_same_paid_kind_not_stacked(client):
    lid = publish(client, user=1)
    pid = invoice(client, 1, lid, "job_vip").json()["payment_id"]
    pay(client, 1, pid, charge="vip-1")
    assert invoice(client, 1, lid, "job_vip").status_code == 400


def test_placement_expires(client):
    lid = publish(client, user=1)
    pid = invoice(client, 1, lid, "job_vip").json()["payment_id"]
    placement = pay(client, 1, pid, charge="vip-2")
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=8)
    with SessionLocal() as db:
        ended = payments.expire_placements(db, at=future)
        ended_ids = [p.id for p in ended]
    assert ended_ids == [placement.id]
    with SessionLocal() as db:
        assert db.get(Placement, placement.id).status == "ended"
    # Once ended, the listing is no longer promoted
    with SessionLocal() as db:
        assert payments.active_placements(db, [lid]) == {}


def test_refund_calls_telegram_and_ends_placement(client):
    lid = publish(client, user=1)
    pid = invoice(client, 1, lid, "job_vip").json()["payment_id"]
    placement = pay(client, 1, pid, charge="refund-me")
    r = client.post(f"/api/admin/payments/{pid}/refund", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["status"] == "refunded"
    method, payload = CALLS[-1]
    assert method == "refundStarPayment"
    assert payload == {"user_id": 1, "telegram_payment_charge_id": "refund-me"}
    with SessionLocal() as db:
        assert db.get(Placement, placement.id).status == "refunded"
    # Second refund is refused
    assert client.post(f"/api/admin/payments/{pid}/refund", headers=ADMIN).status_code == 400


def test_admin_can_change_price(client):
    assert client.put("/api/admin/pricing/job_vip", json={"stars": 120}).status_code == 401
    r = client.put("/api/admin/pricing/job_vip", json={"stars": 120}, headers=ADMIN)
    assert r.status_code == 200
    lid = publish(client, user=1)
    invoice(client, 1, lid, "job_vip")
    assert CALLS[-1][1]["prices"][0]["amount"] == 120


def test_admin_rejects_absurd_price(client):
    r = client.put("/api/admin/pricing/job_vip", json={"stars": 0}, headers=ADMIN)
    assert r.status_code == 400


def test_admin_can_disable_service(client):
    client.put("/api/admin/pricing/job_pin", json={"is_active": False}, headers=ADMIN)
    lid = publish(client, user=1)
    assert invoice(client, 1, lid, "job_pin").status_code == 400
    codes = [s["code"] for s in client.get("/api/services?category=job").json()]
    assert "job_pin" not in codes


def test_stats_and_history(client):
    lid = publish(client, user=1)
    pid = invoice(client, 1, lid, "job_vip").json()["payment_id"]
    pay(client, 1, pid, charge="stat-1")
    stats = client.get("/api/admin/stats", headers=ADMIN).json()
    assert stats["revenue_stars_total"] == 100
    assert stats["payers"] == 1
    assert stats["day"]["sales"] == 1
    history = client.get("/api/me/payments", headers=as_user(1)).json()
    assert history[0]["status"] == "paid"
    assert history[0]["service"] == "VIP-вакансия на 7 дней"
