import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import botadmin as ba
from app import payments
from app.database import SessionLocal, engine
from app.main import app
from app.models import Admin, Payment, Placement, PromoCode
from app.payments import now
from tests.tg import accept_terms, as_user, reset_db

ADMIN = {"X-Admin-Session": "test-session-token"}
CALLS = []


@pytest.fixture()
def client(monkeypatch):
    assert engine.url.drivername == "sqlite"
    reset_db()
    CALLS.clear()

    def fake_telegram(method, payload):
        CALLS.append((method, payload))
        return f"https://t.me/invoice/{payload['payload'][:8]}"

    monkeypatch.setattr(payments, "telegram_call", fake_telegram)
    with TestClient(app) as c:
        for uid in (1, 2):
            accept_terms(c, uid)
        yield c


def publish(client, user=1, category="job"):
    lid = client.post("/api/listings", json={"category": category, "title": "Повар", "city": "Сеул", "description": "ok"},
                      headers=as_user(user)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def promo(client, **kw):
    body = {"code": "spring", "percent": 20}
    body.update(kw)
    return client.post("/api/admin/promos", json=body, headers=ADMIN)


def invoice(client, user, lid, code="job_vip", promo_code=""):
    return client.post("/api/payments/invoice", json={"listing_id": lid, "service_code": code, "promo_code": promo_code}, headers=as_user(user))


def pay(user, payment_id, charge="c1"):
    with SessionLocal() as db:
        p = db.get(Payment, payment_id)
        return payments.process_successful_payment(db, str(user), p.payload, charge, "XTR", p.stars)


# ---- showcase ----

def test_showcase_lists_services_with_text_and_availability(client):
    lid = publish(client)
    data = client.get(f"/api/listings/{lid}/services", headers=as_user(1)).json()
    kinds = {o["kind"]: o for o in data["options"]}
    assert kinds["vip"]["stars"] == 100 and "VIP" in kinds["vip"]["what"] and kinds["vip"]["available"] is True
    assert client.get(f"/api/listings/{lid}/services", headers=as_user(2)).status_code == 404   # not your listing
    # after buying VIP it says "already active" with the end date; bump shows the wait
    r = invoice(client, 1, lid).json()
    pay(1, r["payment_id"])
    r2 = invoice(client, 1, lid, "job_bump").json()
    pay(1, r2["payment_id"], "c2")
    opts = {o["kind"]: o for o in client.get(f"/api/listings/{lid}/services", headers=as_user(1)).json()["options"]}
    assert opts["vip"]["available"] is False and opts["vip"]["reason"] == "Уже действует" and opts["vip"]["active_until"]
    assert opts["bump"]["available"] is False and "Поднять можно через" in opts["bump"]["reason"]
    assert opts["pin"]["available"] is True


def test_owner_sees_running_services_on_the_listing(client):
    lid = publish(client)
    pay(1, invoice(client, 1, lid).json()["payment_id"])
    mine = client.get("/api/me/listings", headers=as_user(1)).json()[0]
    assert mine["placements"][0]["kind"] == "vip"
    assert client.get(f"/api/listings/{lid}").json()["placements"] == []   # visitors do not see them


# ---- promo codes ----

def test_promo_percent_and_fixed_and_floor(client):
    lid = publish(client)
    assert promo(client, code="spring", percent=20).status_code == 201
    r = invoice(client, 1, lid, "job_vip", "  Spring ")
    assert r.status_code == 200 and r.json()["stars"] == 80                       # 100 − 20%, code is not case sensitive
    assert CALLS[-1][1]["prices"][0]["amount"] == 80                               # Telegram is asked for the discounted price
    assert promo(client, code="big", percent=None, fixed_stars=500).status_code == 201
    r = invoice(client, 2, publish(client, 2), "job_vip", "BIG")
    assert r.json()["stars"] == 1                                                  # never below 1 star


def test_promo_rules(client):
    lid = publish(client)
    assert invoice(client, 1, lid, "job_vip", "NOPE").status_code == 400
    promo(client, code="ONCE", percent=50, max_uses=1)
    pid = invoice(client, 1, lid, "job_vip", "once").json()["payment_id"]
    pay(1, pid)
    with SessionLocal() as db:
        assert db.scalar(select(PromoCode).where(PromoCode.code == "ONCE")).used_count == 1
    lid2 = publish(client, 2)
    assert "максимальное" in invoice(client, 2, lid2, "job_vip", "ONCE").json()["detail"]     # limit reached
    promo(client, code="EVERY", percent=10)
    pay(1, invoice(client, 1, lid, "job_pin", "EVERY").json()["payment_id"], "c9")
    lid3 = publish(client)
    assert "уже использовали" in invoice(client, 1, lid3, "job_vip", "EVERY").json()["detail"]  # one use per person


def test_promo_expiry_toggle_and_validation(client):
    lid = publish(client)
    promo(client, code="OLD", percent=10)
    with SessionLocal() as db:
        db.scalar(select(PromoCode).where(PromoCode.code == "OLD")).valid_until = now() - dt.timedelta(days=1)
        db.commit()
    assert "Срок" in invoice(client, 1, lid, "job_vip", "OLD").json()["detail"]
    pid = promo(client, code="OFF", percent=10).json()["id"]
    assert client.put(f"/api/admin/promos/{pid}/toggle", headers=ADMIN).json()["active"] is False
    assert invoice(client, 1, lid, "job_vip", "OFF").status_code == 400
    assert promo(client, code="spring", percent=10, fixed_stars=5).status_code == 422     # one kind of discount only
    assert promo(client, code="no", percent=10).status_code == 422                        # too short
    assert promo(client, code="a b!", percent=10).status_code == 400
    assert promo(client, code="OLD", percent=10).status_code == 409                        # duplicate
    assert client.get("/api/admin/promos", headers=as_user(1)).status_code in (401, 403)  # not for users


def test_promo_check_endpoint(client):
    promo(client, code="HALF", percent=50)
    r = client.get("/api/promo/check", params={"code": "half", "service_code": "job_vip"}, headers=as_user(1))
    assert r.json() == {"code": "HALF", "stars": 50, "was": 100}
    assert client.get("/api/promo/check", params={"code": "zzz", "service_code": "job_vip"}, headers=as_user(1)).status_code == 400
    assert client.get("/api/promo/check", params={"code": "half", "service_code": "nope"}, headers=as_user(1)).status_code == 404


def test_wrong_amount_with_discount_is_refused(client):
    lid = publish(client)
    promo(client, code="SPRING", percent=20)
    pid = invoice(client, 1, lid, "job_vip", "SPRING").json()["payment_id"]
    with SessionLocal() as db:
        p = db.get(Payment, pid)
        with pytest.raises(payments.PaymentError):
            payments.process_successful_payment(db, "1", p.payload, "c1", "XTR", 100)   # paid the full price: mismatch


# ---- receipt ----

def test_receipt_shows_discount_and_is_private(client):
    lid = publish(client)
    promo(client, code="SPRING", percent=20)
    pid = invoice(client, 1, lid, "job_vip", "SPRING").json()["payment_id"]
    pay(1, pid, "charge-x")
    rc = client.get(f"/api/me/payments/{pid}/receipt", headers=as_user(1)).json()
    assert rc["number"] == f"KH-{pid:06d}" and rc["stars"] == 80 and rc["list_stars"] == 100 and rc["discount"] == 20
    assert rc["promo"] == "SPRING" and rc["service"] and rc["listing_title"] == "Повар" and rc["charge_id"] == "charge-x"
    assert client.get(f"/api/me/payments/{pid}/receipt", headers=as_user(2)).status_code == 404
    assert client.get("/api/me/payments", headers=as_user(1)).json()[0]["number"] == f"KH-{pid:06d}"


# ---- reminder before a service ends ----

def test_reminder_one_day_before_end_once_and_not_for_bump(client):
    lid = publish(client)
    pay(1, invoice(client, 1, lid).json()["payment_id"])
    pay(1, invoice(client, 1, lid, "job_bump").json()["payment_id"], "c2")
    with SessionLocal() as db:
        assert payments.placements_ending_soon(db) == []                          # 7 days left
        for p in db.scalars(select(Placement)).all():
            p.ends_at = now() + dt.timedelta(hours=10)
        db.commit()
        soon = payments.placements_ending_soon(db)
        assert [p.kind for p in soon] == ["vip"]                                  # bump lasts a day, no reminder
        assert payments.placements_ending_soon(db) == []                          # only once


# ---- bot admin ----

def test_bot_admin_creates_and_switches_promo(client):
    with SessionLocal() as db:
        db.add(Admin(tg_id="777", role="admin", active=True, created_at=now()))
        db.add(Admin(tg_id="555", role="moderator", active=True, created_at=now()))
        db.commit()
        assert ba.handle(db, 555, "moderator", ["a", "K"]).alert                    # a moderator has no access
        ask = ba.handle(db, 777, "admin", ["a", "K", "new"])
        assert ask.state["kind"] == "promo_new"
        ba.PENDING_INPUT[777] = ask.state
        bad = ba.handle_text(db, 777, "admin", "ЛЕТО")
        assert "Не разобрал" in bad.text and bad.state
        done = ba.handle_text(db, 777, "admin", "SUMMER 15% 5 30")
        assert "Создан промокод SUMMER" in done.text
        row = db.scalar(select(PromoCode).where(PromoCode.code == "SUMMER"))
        assert row.percent == 15 and row.max_uses == 5 and row.valid_until is not None
        ba.PENDING_INPUT[777] = ask.state
        ba.handle_text(db, 777, "admin", "STAR 30⭐")
        assert db.scalar(select(PromoCode).where(PromoCode.code == "STAR")).fixed_stars == 30
        listing = ba.handle(db, 777, "admin", ["a", "K", "t", str(row.id)])
        db.refresh(row)
        assert row.is_active is False and "⚪️ SUMMER" in listing.text
