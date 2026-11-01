import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal, engine
from app.main import app
from app.models import Claim, Listing
from app.payments import now
from tests.tg import as_user, reset_db, accept_terms
from tests.test_catalog import make
import datetime as dt

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def pending(client, n):
    return [make(client, user=1 + i % 3, publish=False, title=f"Объявление {i}") for i in range(n)]


def status(lid):
    with SessionLocal() as db:
        return db.get(Listing, lid).status


def test_preview_skips_claimed_and_disputed_then_confirmation_by_number(client):
    ids = pending(client, 5)
    with SessionLocal() as db:
        db.add(Claim(kind="listing", obj_id=str(ids[0]), staff="555", until=now() + dt.timedelta(minutes=10)))
        db.get(Listing, ids[1]).dispute = True
        db.get(Listing, ids[2]).quarantine = True
        db.commit()
    pv = client.post("/api/admin/batches/preview", json={"status": "pending"}, headers=ADMIN).json()
    assert pv["count"] == 2 and {s["id"] for s in pv["skipped"]} == set(ids[:3])
    body = {"target": {"status": "pending"}, "decision": "approve", "confirm_count": 5}
    assert client.post("/api/admin/batches", json=body, headers=ADMIN).status_code == 409           # the number must match what is touched
    r = client.post("/api/admin/batches", json=body | {"confirm_count": 2}, headers=ADMIN)
    assert r.status_code == 201 and r.json()["total"] == 2


def test_steps_progress_stop_and_undo(client):
    ids = pending(client, 30)
    r = client.post("/api/admin/batches", json={"target": {"ids": ids}, "decision": "approve", "confirm_count": 30}, headers=ADMIN).json()
    bid = r["id"]
    s1 = client.post(f"/api/admin/batches/{bid}/step", headers=ADMIN).json()
    assert s1["done"] == 25 and s1["left"] == 5 and s1["status"] == "running"
    assert client.post(f"/api/admin/batches/{bid}/stop", headers=ADMIN).json()["status"] == "stopped"
    assert client.post(f"/api/admin/batches/{bid}/step", headers=ADMIN).json()["done"] == 25                # stopped: nothing more happens
    assert sum(status(i) == "approved" for i in ids) == 25
    # one object was changed by somebody else afterwards: undo leaves it alone
    client.post(f"/api/admin/listings/{ids[0]}/hide?reason=позже", headers=ADMIN)
    u = client.post(f"/api/admin/batches/{bid}/undo", headers=ADMIN).json()
    assert u["returned"] >= 23 and u["left_as_is"] >= 1
    assert sum(status(i) == "approved" for i in ids) <= 1
    assert client.post(f"/api/admin/batches/{bid}/undo", headers=ADMIN).status_code == 409


def test_reject_needs_a_reason_and_dispute_blocks_a_moderator(client):
    ids = pending(client, 2)
    assert client.post("/api/admin/batches", json={"target": {"ids": ids}, "decision": "reject", "confirm_count": 2}, headers=ADMIN).status_code == 400
    r = client.post(f"/api/admin/listings/{ids[0]}/flags", json={"dispute": True, "reason": "автор спорит"}, headers=ADMIN)
    assert r.json()["dispute"] is True
    assert client.post(f"/api/admin/listings/{ids[0]}/flags", json={"dispute": False}, headers=ADMIN).status_code == 400    # reason needed
    assert client.post(f"/api/admin/listings/{ids[0]}/flags", json={"dispute": False, "reason": "решено"}, headers=ADMIN).json()["dispute"] is False


def test_quarantine_hides_from_search_until_a_moderator_lifts_it(client):
    lid = make(client, user=1, title="Cook wanted")
    assert [x["id"] for x in client.get("/api/catalog", params={"q": "cook"}).json()["items"]] == [lid]
    client.post(f"/api/admin/listings/{lid}/flags", json={"quarantine": True, "reason": "проверка документов"}, headers=ADMIN)
    assert client.get("/api/catalog", params={"q": "cook"}).json()["items"] == []
    client.post(f"/api/admin/listings/{lid}/flags", json={"quarantine": False, "reason": "документы в порядке"}, headers=ADMIN)
    assert [x["id"] for x in client.get("/api/catalog", params={"q": "cook"}).json()["items"]] == [lid]
