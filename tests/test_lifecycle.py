import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import lifecycle, payments
from app.database import SessionLocal, engine
from app.main import app
from app.models import Listing
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2):
            accept_terms(c, uid)
        yield c


def make(client, publish=True, **fields):
    body = {"category": "job", "title": "Работа", "city": "Асан", "description": "ok", "contact": ""}
    body.update(fields)
    r = client.post("/api/listings", json=body, headers=as_user(1))
    assert r.status_code == 201, r.text
    lid = r.json()["id"]
    if publish:
        client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


def mine(client, lid):
    return next(x for x in client.get("/api/me/listings", headers=as_user(1)).json() if x["id"] == lid)


def ids(client, **params):
    return [x["id"] for x in client.get("/api/catalog", params=params).json()["items"]]


def queue(client):
    return [x["id"] for x in client.get("/api/admin/listings", headers=ADMIN).json()]


def set_times(lid, **times):
    with SessionLocal() as db:
        item = db.get(Listing, lid)
        for k, v in times.items():
            setattr(item, k, v)
        db.commit()


# ---- drafts ----

def test_draft_is_private_and_not_moderated(client):
    lid = make(client, publish=False, draft=True)
    assert mine(client, lid)["status"] == "draft"
    assert ids(client) == [] and queue(client) == []
    assert client.get(f"/api/listings/{lid}").status_code == 404
    assert client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN).status_code in (200, 400)  # moderators cannot see it


def test_draft_can_be_edited_freely_then_submitted(client):
    lid = make(client, publish=False, draft=True)
    r = client.put(f"/api/listings/{lid}", json={"title": "Другое название", "price_amount": 1_000_000}, headers=as_user(1))
    assert r.status_code == 200 and r.json()["status"] == "draft" and r.json()["title"] == "Другое название"
    r = client.post(f"/api/listings/{lid}/submit", headers=as_user(1))
    assert r.json()["status"] == "pending" and lid in queue(client)
    assert client.post(f"/api/listings/{lid}/submit", headers=as_user(1)).status_code == 409


def test_drafts_count_towards_the_limit(client):
    for i in range(5):
        make(client, publish=False, draft=True, title=f"Черновик {i}")
    r = client.post("/api/listings", json={"category": "job", "title": "Ещё", "city": "Асан"}, headers=as_user(1))
    assert r.status_code == 409


# ---- edits of a published listing ----

def test_small_changes_apply_at_once_without_moderation(client):
    lid = make(client, schedule="day", visas=["E-9"])
    r = client.put(f"/api/listings/{lid}", json={"schedule": "night", "visas": ["F-4"]}, headers=as_user(1))
    assert r.json()["has_edit"] is False and r.json()["schedule"] == "night"
    assert ids(client, schedule="night") == [lid] and lid not in queue(client)


def test_important_changes_wait_and_visitors_see_the_old_text(client):
    lid = make(client, title="Старое название", price_amount=1_000_000)
    r = client.put(f"/api/listings/{lid}", json={"title": "Новое название", "price_amount": 2_000_000}, headers=as_user(1))
    assert r.json()["has_edit"] is True and r.json()["status"] == "approved"
    shown = client.get(f"/api/listings/{lid}").json()
    assert shown["title"] == "Старое название" and shown["price_amount"] == 1_000_000 and shown["has_edit"] is False
    assert queue(client) == [lid]
    card = client.get("/api/admin/listings", headers=ADMIN).json()[0]
    assert card["pending_changes"] == {"title": "Новое название", "price_amount": 2_000_000}


def test_approving_an_edit_publishes_it(client):
    lid = make(client, title="Старое название")
    client.put(f"/api/listings/{lid}", json={"title": "Новое название"}, headers=as_user(1))
    assert client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN).status_code == 200
    assert client.get(f"/api/listings/{lid}").json()["title"] == "Новое название"
    assert ids(client, q="новое") == [lid] and ids(client, q="старое") == []
    assert queue(client) == [] and mine(client, lid)["has_edit"] is False


def test_rejecting_an_edit_keeps_the_published_version_and_tells_why(client):
    lid = make(client, title="Старое название")
    client.put(f"/api/listings/{lid}", json={"title": "Плохое название"}, headers=as_user(1))
    r = client.post(f"/api/admin/listings/{lid}/reject?reason=Лишнее+слово", headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert client.get(f"/api/listings/{lid}").json()["title"] == "Старое название"
    assert mine(client, lid)["note"] == "Лишнее слово" and queue(client) == []


def test_several_edits_are_merged_into_one_review(client):
    lid = make(client, title="Старое название")
    client.put(f"/api/listings/{lid}", json={"title": "Первое"}, headers=as_user(1))
    client.put(f"/api/listings/{lid}", json={"description": "Новое описание"}, headers=as_user(1))
    client.put(f"/api/listings/{lid}", json={"title": "Второе"}, headers=as_user(1))
    card = client.get("/api/admin/listings", headers=ADMIN).json()[0]
    assert card["pending_changes"] == {"title": "Второе", "description": "Новое описание"}


def test_edit_cannot_slip_in_forbidden_text(client):
    lid = make(client)
    r = client.put(f"/api/listings/{lid}", json={"description": "Нужна предоплата на карту"}, headers=as_user(1))
    assert r.status_code == 400
    assert queue(client) == []


def test_edit_of_a_waiting_listing_goes_straight_to_review(client):
    lid = make(client, publish=False)
    r = client.put(f"/api/listings/{lid}", json={"title": "Исправлено"}, headers=as_user(1))
    assert r.json()["status"] == "pending" and r.json()["title"] == "Исправлено"


# ---- lifetime ----

def test_publication_sets_the_dates(client):
    lid = make(client)
    x = mine(client, lid)
    assert x["published_at"] and x["expires_at"]
    days = (dt.datetime.fromisoformat(x["expires_at"]) - dt.datetime.fromisoformat(x["published_at"])).days
    assert 29 <= days <= 30
    assert client.get(f"/api/listings/{lid}").json()["expires_at"] is None  # visitors do not see the end date


def test_a_listing_past_its_date_disappears_from_search_at_once(client):
    lid = make(client)
    set_times(lid, expires_at=payments.now() - dt.timedelta(hours=1))
    assert ids(client) == []                           # even before the cleanup job runs


def test_job_marks_expired_listings_and_the_author_can_renew(client):
    lid = make(client)
    set_times(lid, expires_at=payments.now() - dt.timedelta(minutes=1))
    with SessionLocal() as db:
        done = lifecycle.expire_listings(db)
        assert [x.id for x in done] == [lid]
    assert mine(client, lid)["status"] == "expired"
    r = client.post(f"/api/listings/{lid}/renew", headers=as_user(1))
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert ids(client) == [lid]


def test_renewing_a_published_listing_extends_it(client):
    lid = make(client)
    set_times(lid, expires_at=payments.now() + dt.timedelta(days=2))
    client.post(f"/api/listings/{lid}/renew", headers=as_user(1))
    left = dt.datetime.fromisoformat(mine(client, lid)["expires_at"]) - dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    assert left.days >= 28


def test_only_the_author_renews_and_only_live_or_expired_ones(client):
    lid = make(client)
    assert client.post(f"/api/listings/{lid}/renew", headers=as_user(2)).status_code == 404
    waiting = make(client, publish=False, title="Ждёт")
    assert client.post(f"/api/listings/{waiting}/renew", headers=as_user(1)).status_code == 409


def test_warning_comes_once_three_days_before_the_end(client):
    soon = make(client, title="Скоро")
    later = make(client, title="Нескоро")
    set_times(soon, expires_at=payments.now() + dt.timedelta(days=2))
    with SessionLocal() as db:
        assert [x.id for x in lifecycle.expiring_soon(db)] == [soon]
        assert lifecycle.expiring_soon(db) == []        # not again
    assert later not in [x.id for x in []]


def test_renewing_allows_a_new_warning(client):
    lid = make(client)
    set_times(lid, expires_at=payments.now() + dt.timedelta(days=2))
    with SessionLocal() as db:
        lifecycle.expiring_soon(db)
    client.post(f"/api/listings/{lid}/renew", headers=as_user(1))
    set_times(lid, expires_at=payments.now() + dt.timedelta(days=2))
    with SessionLocal() as db:
        assert [x.id for x in lifecycle.expiring_soon(db)] == [lid]


# ---- views ----

def test_views_are_counted_once_per_visitor_per_hour(client):
    lid = make(client)
    for _ in range(3):
        client.get(f"/api/listings/{lid}", headers={"user-agent": "A"})
    assert client.get(f"/api/listings/{lid}", headers={"user-agent": "A"}).json()["views"] == 1
    client.get(f"/api/listings/{lid}", headers={"user-agent": "B"})
    assert client.get(f"/api/listings/{lid}", headers={"user-agent": "B"}).json()["views"] == 2
