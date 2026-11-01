import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import lifecycle, payments
from app.database import SessionLocal, engine
from app.main import app
from app.models import Listing, Placement
from tests.tg import as_user, reset_db, accept_terms
from tests.test_catalog import ADMIN, make


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2):
            accept_terms(c, uid)
        yield c


def test_paid_time_stops_while_listing_is_hidden_and_is_added_back(client):
    lid = make(client)
    now = payments.now()
    with SessionLocal() as db:
        db.add(Placement(listing_id=lid, kind="vip", starts_at=now, ends_at=now + dt.timedelta(days=7), status="active"))
        db.commit()
        item = db.get(Listing, lid)
        item.status = "hidden"
        db.commit()
        pl = db.query(Placement).one()
        assert pl.frozen_at is not None
        assert payments.expire_placements(db, now + dt.timedelta(days=30)) == []        # a stopped clock does not run out
        end_before = payments.aware(pl.ends_at)
        pl.frozen_at = now - dt.timedelta(days=2)                                         # it was stopped for two days
        item.status = "approved"
        db.commit()
        pl = db.query(Placement).one()
        assert pl.frozen_at is None
        assert abs((payments.aware(pl.ends_at) - end_before - (payments.now() - (now - dt.timedelta(days=2)))).total_seconds()) < 5


def test_week_reminder_is_sent_once(client):
    lid = make(client)
    now = payments.now()
    with SessionLocal() as db:
        db.get(Listing, lid).expires_at = now + dt.timedelta(days=6)
        db.commit()
        assert [x.id for x in lifecycle.expiring_week(db)] == [lid]
        assert lifecycle.expiring_week(db) == []
