import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import duty, rules
from app.admin_auth import _hash
from app.database import SessionLocal, engine
from app.main import app
from app.models import Admin, AdminSession, Notification
from app.payments import now
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    rules.reset_cache()
    with TestClient(app) as c:
        accept_terms(c, 1)
        with SessionLocal() as db:
            db.add(Admin(tg_id="777", role="support", active=True, created_at=now()))
            db.add(Admin(tg_id="778", role="support", active=True, created_at=now()))
            db.add(AdminSession(token_hash=_hash("duty-token"), tg_id="777", expires_at=now() + dt.timedelta(hours=1)))
            db.commit()
        yield c
    rules.reset_cache()


def shift(client, tg="777", area="support", start=-1, end=2):
    n = now()
    return client.post("/api/admin/duty", json={"area": area, "tg_id": tg, "starts_at": (n + dt.timedelta(hours=start)).isoformat(),
                                                "ends_at": (n + dt.timedelta(hours=end)).isoformat()}, headers=ADMIN)


def texts(tg):
    with SessionLocal() as db:
        return [n.text for n in db.query(Notification).filter_by(user_tg_id=tg)]


def test_only_the_person_on_duty_is_told_and_overlaps_are_refused(client):
    assert shift(client).status_code == 201
    assert shift(client, tg="778").status_code == 409                                  # somebody is already on duty then
    assert shift(client, tg="999", area="super").status_code == 201                    # an administrator may take another area
    assert client.post("/api/admin/duty", json={"area": "money", "tg_id": "777", "starts_at": now().isoformat(),
                                                "ends_at": (now() + dt.timedelta(hours=1)).isoformat()}, headers=ADMIN).status_code == 400      # a support has no right to money duty
    assert client.post("/api/tickets", json={"kind": "support", "text": "Не работает оплата"}, headers=as_user(1)).status_code == 201
    assert any("Вы дежурный" in t for t in texts("777"))
    assert not any("обращение" in t.lower() for t in texts("778"))                     # not told: not on duty
    ov = client.get("/api/admin/duty", headers=ADMIN).json()
    assert ov["areas"]["support"]["on_duty"] == "777" and len(ov["open_alerts"]) == 1


def test_nobody_on_duty_means_everybody_with_the_role_as_before(client):
    client.post("/api/tickets", json={"kind": "support", "text": "Вопрос без дежурного"}, headers=as_user(1))
    assert any("обращение" in t.lower() for t in texts("778")) and any("обращение" in t.lower() for t in texts("777"))


def test_unseen_alert_goes_up_and_opening_the_panel_calms_it(client):
    shift(client)
    client.post("/api/tickets", json={"kind": "support", "text": "Срочно нужна помощь"}, headers=as_user(1))
    with SessionLocal() as db:
        assert duty.escalate(db, now() + dt.timedelta(minutes=10)) == 0               # too early
        assert duty.escalate(db, now() + dt.timedelta(minutes=31)) == 1
    assert any("не ответил" in t for t in texts("778"))                                # the whole area is told
    with SessionLocal() as db:
        assert duty.escalate(db, now() + dt.timedelta(minutes=61)) == 1
    assert any("Никто не ответил" in t for t in texts("999"))                          # then the super administrator
    # a new alert, but the duty person opens the panel
    client.post("/api/tickets", json={"kind": "support", "text": "Ещё один вопрос тут"}, headers=as_user(1)) 
    r = client.get("/api/admin/counters", headers={"X-Admin-Session": "duty-token"})
    assert r.status_code == 200
    with SessionLocal() as db:
        assert duty.escalate(db, now() + dt.timedelta(minutes=200)) == 0
