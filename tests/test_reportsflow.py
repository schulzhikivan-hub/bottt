import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import reportsflow, rules
from app.database import SessionLocal, engine
from app.main import app
from app.models import Listing, Notification, Report
from app.payments import now
from tests.tg import as_user, reset_db, accept_terms
from tests.test_catalog import make

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    rules.reset_cache()
    with TestClient(app) as c:
        for uid in (1, 2, 3, 4):
            accept_terms(c, uid)
        yield c
    rules.reset_cache()


def complain(client, lid, users=(2, 3, 4)):
    for u in users:
        assert client.post("/api/reports", json={"target_type": "listing", "target_id": str(lid), "reason": "fake"}, headers=as_user(u)).status_code == 201


def test_group_decision_answers_everyone_and_reject_brings_the_listing_back(client):
    lid = make(client, user=1)
    complain(client, lid)
    with SessionLocal() as db:
        assert db.get(Listing, lid).status == "hidden"                         # three people hid it
    r = client.post("/api/admin/reports/resolve-group", json={"target_type": "listing", "target_id": str(lid), "decision": "reject", "reason": "всё в порядке"}, headers=ADMIN)
    assert r.json() == {"closed": 3, "answers": 3}
    with SessionLocal() as db:
        assert db.get(Listing, lid).status == "approved"
        who = {n.user_tg_id for n in db.query(Notification).filter(Notification.text.like("Мы проверили вашу жалобу%"))}
        assert who == {"2", "3", "4"}
    assert client.post("/api/admin/reports/resolve-group", json={"target_type": "listing", "target_id": str(lid), "decision": "reject"}, headers=ADMIN).status_code == 404


def test_group_resolve_hides_once_and_tells_all(client):
    lid = make(client, user=1)
    complain(client, lid, (2, 3))
    client.post("/api/admin/reports/resolve-group", json={"target_type": "listing", "target_id": str(lid), "decision": "resolve"}, headers=ADMIN)
    with SessionLocal() as db:
        assert db.get(Listing, lid).status == "hidden"
        assert db.query(Report).filter_by(status="resolved").count() == 2


def test_old_complaints_close_by_themselves_when_switched_on(client):
    lid = make(client, user=1)
    complain(client, lid)
    with SessionLocal() as db:
        for r in db.query(Report):
            r.created_at = now() - dt.timedelta(days=5)
        db.commit()
        reportsflow.auto_close(db)
        assert db.query(Report).filter_by(status="new").count() == 3          # off by default: nothing happens
    client.put("/api/admin/rules/reports.auto_days", json={"value": 3}, headers=ADMIN)
    rules.reset_cache()
    with SessionLocal() as db:
        reportsflow.auto_close(db)
        assert db.query(Report).filter_by(status="new").count() == 0
        assert db.get(Listing, lid).status == "approved"                       # returned: the moderator did not decide in time
        assert db.query(Notification).filter(Notification.text.like("%закрыта автоматически%")).count() == 3
