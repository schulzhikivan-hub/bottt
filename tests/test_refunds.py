import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import Payment, User
from tests.tg import as_user, reset_db, accept_terms
from tests.test_dating import ADMIN, publish


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2):
            accept_terms(c, uid)
        yield c


def paid(client, uid=1):
    publish(client, uid)
    with SessionLocal() as db:
        db.get(User, str(uid)).balance_stars = 300
        db.commit()
    return client.post("/api/dating/invoice", json={"service_code": "dating_vip", "use_balance": True}, headers=as_user(uid)).json()["payment_id"]


def test_refund_process_statuses_and_no_double_refund(client):
    pid = paid(client)
    r = client.post(f"/api/me/payments/{pid}/refund-request", json={"reason": "передумал"}, headers=as_user(1))
    assert r.status_code == 201 and r.json()["status"] == "new"
    assert client.post(f"/api/me/payments/{pid}/refund-request", json={"reason": "ещё раз"}, headers=as_user(1)).status_code == 409
    assert client.post(f"/api/me/payments/{pid}/refund-request", json={"reason": "чужой"}, headers=as_user(2)).status_code == 404
    rid = r.json()["id"]
    assert client.get("/api/admin/refunds", headers=ADMIN).json()[0]["id"] == rid
    assert client.post(f"/api/admin/refunds/{rid}/take", headers=ADMIN).json()["status"] == "in_progress"
    ok = client.post(f"/api/admin/refunds/{rid}/approve", json={"mode": "balance", "comment": "вернули"}, headers=ADMIN)
    assert ok.status_code == 200 and ok.json()["status"] in {"approved", "partially_refunded"} and 60 <= ok.json()["amount"] <= 70
    assert "не откатываются" in ok.json()["note"]
    with SessionLocal() as db:
        assert db.get(Payment, pid).refunded_amount == ok.json()["amount"] and db.get(Payment, pid).status == "refunded"
    assert client.post(f"/api/admin/refunds/{rid}/approve", json={"mode": "balance"}, headers=ADMIN).status_code == 409      # closed
    assert client.post(f"/api/admin/payments/{pid}/refund?mode=balance", headers=ADMIN).status_code == 400                  # no second refund by the old button
    mine = client.get("/api/me/refunds", headers=as_user(1)).json()
    assert mine[0]["status"] == ok.json()["status"] and mine[0]["answer"] == "вернули"
    csv = client.get("/api/admin/refunds/export", headers=ADMIN)
    assert csv.status_code == 200 and "передумал" in csv.text


def test_reject_needs_a_reason_and_cancel_takes_the_credit_back(client):
    pid = paid(client)
    rid = client.post(f"/api/me/payments/{pid}/refund-request", json={"reason": "ошибка"}, headers=as_user(1)).json()["id"]
    assert client.post(f"/api/admin/refunds/{rid}/reject", json={"reason": "x"}, headers=ADMIN).status_code == 422
    assert client.post(f"/api/admin/refunds/{rid}/reject", json={"reason": "услуга уже использована"}, headers=ADMIN).json()["status"] == "rejected"
    assert client.post(f"/api/admin/refunds/{rid}/reject", json={"reason": "ещё раз"}, headers=ADMIN).status_code == 409
    pid2 = client.post("/api/dating/invoice", json={"service_code": "dating_bump", "use_balance": True}, headers=as_user(1)).json()["payment_id"]
    rid2 = client.post(f"/api/me/payments/{pid2}/refund-request", json={"reason": "тест"}, headers=as_user(1)).json()["id"]
    client.post(f"/api/admin/refunds/{rid2}/approve", json={"mode": "balance"}, headers=ADMIN)
    with SessionLocal() as db:
        before = db.get(User, "1").balance_stars
    c = client.post(f"/api/admin/refunds/{rid2}/cancel", headers=ADMIN)
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    with SessionLocal() as db:
        assert db.get(User, "1").balance_stars < before and db.get(Payment, pid2).refunded_amount == 0
    assert client.post(f"/api/admin/refunds/{rid2}/cancel", headers=ADMIN).status_code == 409


def test_top_up_is_not_refundable_by_request(client):
    publish(client, 1)
    client.post("/api/admin/users/1/stars", json={"stars": 50, "reason": "подарок"}, headers=ADMIN)
    with SessionLocal() as db:
        p = db.query(Payment).filter_by(service_code="topup").one()
        pid = p.id
    assert client.post(f"/api/me/payments/{pid}/refund-request", json={"reason": "верните"}, headers=as_user(1)).status_code == 409
