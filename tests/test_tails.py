import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import retention, support
from app.database import SessionLocal, engine
from app.main import app
from app.models import Article, DatingView, Heartbeat, Notification, Ticket, TicketMessage
from app.payments import now
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        accept_terms(c, 1)
        yield c


def test_retention_removes_only_what_is_old(client):
    old, fresh = now() - dt.timedelta(days=100), now() - dt.timedelta(days=5)
    with SessionLocal() as db:
        db.add_all([Notification(user_tg_id="1", kind="system", text="old", created_at=old),
                    Notification(user_tg_id="1", kind="system", text="new", created_at=fresh),
                    DatingView(profile_id=1, viewer_tg="2", at=old), DatingView(profile_id=1, viewer_tg="3", at=fresh)])
        t = Ticket(user_tg_id="1", status="closed", updated_at=now() - dt.timedelta(days=400))
        db.add(t)
        db.flush()
        db.add(TicketMessage(ticket_id=t.id, text="x"))
        db.commit()
        out = retention.purge(db)
        assert out == {"notifications": 1, "views": 1, "tickets": 1}
        assert [n.text for n in db.query(Notification)] == ["new"]
        assert db.query(TicketMessage).count() == 0


def test_retention_runs_once_a_day(client):
    with SessionLocal() as db:
        assert retention.run_daily(db) is not None
        assert retention.run_daily(db) is None
        db.get(Heartbeat, "retention").at = now() - dt.timedelta(hours=25)
        db.commit()
        assert retention.run_daily(db) is not None


def test_bot_support_request_becomes_a_ticket(client):
    with SessionLocal() as db:
        number = support.open_ticket_from_text(db, "1", "Не продлевается объявление", "Отзыв: ")
        m = db.query(TicketMessage).filter_by(ticket_id=number).one()
        assert m.text.startswith("Отзыв: Не продлевается") and m.staff is False
    assert client.get("/api/tickets", headers=as_user(1)).json()[0]["id"] == number


def test_old_article_is_marked_stale(client):
    with SessionLocal() as db:
        db.add_all([Article(section="visa", title="Старая", body="b", source_url="https://x.kr", published=True, checked_at=now() - dt.timedelta(days=300)),
                    Article(section="visa", title="Свежая", body="b", source_url="https://x.kr", published=True, checked_at=now() - dt.timedelta(days=10))])
        db.commit()
    rows = {a["title"]: a["stale"] for a in client.get("/api/articles").json()}
    assert rows == {"Старая": True, "Свежая": False}
