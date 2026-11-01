import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import guard
from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import Notification
from tests.tg import as_user, reset_db, accept_terms

UTC = dt.timezone.utc
NIGHT = dt.datetime(2026, 11, 1, 14, 0, tzinfo=UTC)        # 23:00 in Korea
MORNING = dt.datetime(2026, 11, 1, 1, 0, tzinfo=UTC)        # 10:00 in Korea


@pytest.fixture()
def client(monkeypatch):
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        accept_terms(c, 1)
        yield c


def test_quiet_hours_are_korean_time(monkeypatch):
    monkeypatch.setattr(settings, "quiet_hours", "22-9")
    assert guard.is_quiet(NIGHT) and not guard.is_quiet(MORNING)
    monkeypatch.setattr(settings, "quiet_hours", "")
    assert not guard.is_quiet(NIGHT)


def test_night_holds_ordinary_notices_but_not_critical(client, monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "quiet_hours", "22-9")
    monkeypatch.setattr(settings, "notifications_enabled", True)
    monkeypatch.setattr(guard, "is_quiet", lambda at=None: True)
    monkeypatch.setattr(guard, "send_telegram", lambda tg, text: sent.append(text) or True)
    with SessionLocal() as db:
        guard.notify_user(db, "1", "новое сообщение", "messages")
        guard.notify_user(db, "1", "платёж принят", "payments")
        assert [n.text for n in db.query(Notification).filter_by(tg_pending=True)] == ["новое сообщение"]
    assert sent == ["платёж принят"]
    # the feed keeps both
    assert len(client.get("/api/me/notifications", headers=as_user(1)).json()["items"]) == 2


def test_flush_sends_one_digest_after_the_night(client, monkeypatch):
    monkeypatch.setattr(settings, "quiet_hours", "22-9")
    with SessionLocal() as db:
        for t in ("a", "b", "c"):
            n = guard.record_notification(db, "1", t, "messages")
            guard.mark_pending(db, n)
        assert guard.flush_pending(db, NIGHT) == []                                   # still night
        out = guard.flush_pending(db, MORNING)
        assert len(out) == 1 and out[0][0] == "1" and "• a" in out[0][1] and "(3)" in out[0][1]
        assert guard.flush_pending(db, MORNING) == []                                 # nothing is sent twice


def test_hourly_mode_and_validation(client, monkeypatch):
    monkeypatch.setattr(settings, "quiet_hours", "")
    assert client.put("/api/me/notify-prefs", json={"mode": "weekly"}, headers=as_user(1)).status_code == 422
    view = client.put("/api/me/notify-prefs", json={"mode": "hourly"}, headers=as_user(1)).json()
    assert view["mode"] == "hourly" and "payments" in view["critical"]
    with SessionLocal() as db:
        assert guard.hold_for_telegram(db, "1", "messages") is True
        assert guard.hold_for_telegram(db, "1", "moderation") is False
        n = guard.record_notification(db, "1", "x", "messages")
        guard.mark_pending(db, n)
        assert len(guard.flush_pending(db, MORNING)) == 1
        n = guard.record_notification(db, "1", "y", "messages")
        guard.mark_pending(db, n)
        assert guard.flush_pending(db, MORNING + dt.timedelta(minutes=20)) == []        # an hour has not passed
        assert len(guard.flush_pending(db, MORNING + dt.timedelta(hours=2))) == 1
