import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal, engine
from app.main import app
from app.models import ClientError
from tests.tg import as_user, reset_db, accept_terms
from tests.test_catalog import make

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def test_server_load_numbers_for_the_dashboard(client):
    m = client.get("/api/admin/system/metrics", headers=ADMIN).json()
    assert m["cpu"]["cores"] >= 1 and m["cpu"]["level"] in {"ok", "warn", "bad"}
    assert m["disk"]["total"] > 0 and 0 <= m["disk"]["pct"] <= 100 and m["uptime_s"] > 0
    assert m["memory"]["level"] in {"ok", "warn", "bad", "na"} and "app_rss" in m["memory"]
    assert client.get("/api/admin/system/metrics").status_code == 401


def test_errors_are_grouped_and_can_be_reset(client):
    with SessionLocal() as db:
        for i in range(3):
            db.add(ClientError(user_tg_id=str(i), where="/api/dating", status=500, message="Внутренняя ошибка", request_id=f"r{i}"))
        db.add(ClientError(user_tg_id="9", where="promise", status=0, message="WebAppMethodUnsupported"))
        db.commit()
    g = client.get("/api/admin/client-errors/groups", headers=ADMIN).json()
    assert {(x["where"], x["count"], x["people"]) for x in g} == {("/api/dating", 3, 3), ("promise", 1, 1)}
    one = client.get("/api/admin/client-errors", headers=ADMIN).json()[0]["id"]
    assert client.delete(f"/api/admin/client-errors/{one}", headers=ADMIN).json() == {"deleted": 1}
    assert client.delete(f"/api/admin/client-errors/{one}", headers=ADMIN).status_code == 404
    r = client.post("/api/admin/client-errors/clear", json={"mode": "group", "where": "/api/dating", "status": 500, "message": "Внутренняя ошибка"}, headers=ADMIN)
    assert r.json()["deleted"] >= 2
    assert client.post("/api/admin/client-errors/clear", json={"mode": "group"}, headers=ADMIN).status_code == 400
    client.post("/api/admin/client-errors/clear", json={"mode": "all"}, headers=ADMIN)
    assert client.get("/api/admin/client-errors", headers=ADMIN).json() == []


def test_suspicious_block_finds_complaints_and_shared_contacts_without_showing_them(client):
    a = make(client, user=1, contact="+82 10 1234 5678")
    b = make(client, user=2, contact="+82 10 1234 5678")
    for reporter in (2, 3):
        client.post("/api/reports", json={"target_type": "user", "target_id": "1", "reason": "spam"}, headers=as_user(reporter))
    s = client.get("/api/admin/suspicious", headers=ADMIN).json()
    assert s["reported"][0]["tg_id"] == "1" and s["reported"][0]["reports"] == 2
    assert s["shared_contact"][0]["accounts"] == 2 and "1234" not in s["shared_contact"][0]["contact"]       # masked
    assert a != b
    c = client.get("/api/admin/counters", headers=ADMIN).json()
    assert "refunds" in c and "companies" in c
