import pytest
from fastapi.testclient import TestClient

from app.database import engine
from app.main import app
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Key": "test-admin-key", "X-Admin-Name": "Test Admin"}
ITEM = {"category": "marketplace", "title": "Стол письменный", "city": "Сеул",
        "price": "50 000 ₩", "description": "Хорошее состояние", "contact": "@seller"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    reset_db()
    with TestClient(app) as c:
        yield c


def approved(client, user=1):
    lid = client.post("/api/listings", json=ITEM, headers=as_user(user)).json()["id"]
    client.post(f"/api/admin/listings/{lid}/approve", headers=ADMIN)
    return lid


# ---- consent (ТЗ § 4.2) ----

def test_writing_requires_consent(client):
    r = client.post("/api/listings", json=ITEM, headers=as_user(1))
    assert r.status_code == 403
    assert "соглашение" in r.json()["detail"]
    accept_terms(client, 1)
    assert client.post("/api/listings", json=ITEM, headers=as_user(1)).status_code == 201


def test_consent_must_be_true(client):
    r = client.post("/api/me/consent", json={"accept": False}, headers=as_user(1))
    assert r.status_code == 400


def test_me_reports_consent_state(client):
    assert client.get("/api/me", headers=as_user(1)).json()["consent"] is False
    accept_terms(client, 1)
    assert client.get("/api/me", headers=as_user(1)).json()["consent"] is True


# ---- reports (ТЗ § 9.3) ----

def test_reports_hide_listing_after_three_reporters(client):
    accept_terms(client, 1)
    for u in (2, 3, 4):
        accept_terms(client, u)
    lid = approved(client, user=1)
    for u in (2, 3):
        r = client.post("/api/reports", json={"target_type": "listing", "target_id": str(lid),
                                              "reason": "spam"}, headers=as_user(u))
        assert r.status_code == 201
    assert any(x["id"] == lid for x in client.get("/api/listings").json())
    client.post("/api/reports", json={"target_type": "listing", "target_id": str(lid),
                                      "reason": "fraud"}, headers=as_user(4))
    assert all(x["id"] != lid for x in client.get("/api/listings").json())


def test_one_reporter_counts_once(client):
    accept_terms(client, 1)
    accept_terms(client, 2)
    lid = approved(client, user=1)
    for _ in range(5):
        client.post("/api/reports", json={"target_type": "listing", "target_id": str(lid),
                                          "reason": "spam"}, headers=as_user(2))
    assert any(x["id"] == lid for x in client.get("/api/listings").json())


def test_cannot_report_own_listing_or_unknown_reason(client):
    accept_terms(client, 1)
    accept_terms(client, 2)
    lid = approved(client, user=1)
    own = client.post("/api/reports", json={"target_type": "listing", "target_id": str(lid),
                                            "reason": "spam"}, headers=as_user(1))
    assert own.status_code == 400
    bad = client.post("/api/reports", json={"target_type": "listing", "target_id": str(lid),
                                            "reason": "whatever"}, headers=as_user(2))
    assert bad.status_code == 400


def test_moderator_resolves_user_report_blocks_user(client):
    accept_terms(client, 1)
    accept_terms(client, 2)
    client.post("/api/reports", json={"target_type": "user", "target_id": "1",
                                      "reason": "fraud"}, headers=as_user(2))
    reports = client.get("/api/admin/reports", headers=ADMIN).json()
    assert len(reports) == 1
    r = client.post(f"/api/admin/reports/{reports[0]['id']}/resolve", headers=ADMIN)
    assert r.status_code == 200
    # User 1 is now blocked from writing
    assert client.post("/api/listings", json=ITEM, headers=as_user(1)).status_code == 403


def test_report_decision_is_final(client):
    accept_terms(client, 1)
    accept_terms(client, 2)
    client.post("/api/reports", json={"target_type": "user", "target_id": "1",
                                      "reason": "spam"}, headers=as_user(2))
    rid = client.get("/api/admin/reports", headers=ADMIN).json()[0]["id"]
    client.post(f"/api/admin/reports/{rid}/reject", headers=ADMIN)
    assert client.post(f"/api/admin/reports/{rid}/resolve", headers=ADMIN).status_code == 409


# ---- blocks ----

def test_block_stops_new_chats_and_messages(client):
    accept_terms(client, 1)
    accept_terms(client, 2)
    lid = approved(client, user=1)
    conv = client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]
    client.post("/api/users/2/block", headers=as_user(1))
    assert client.post(f"/api/conversations/{conv}/messages", json={"text": "hi"},
                       headers=as_user(2)).status_code == 403
    assert client.get("/api/conversations", headers=as_user(1)).json() == []
    client.delete("/api/users/2/block", headers=as_user(1))
    assert client.post(f"/api/conversations/{conv}/messages", json={"text": "hi"},
                       headers=as_user(2)).status_code == 201


def test_cannot_block_yourself(client):
    accept_terms(client, 1)
    assert client.post("/api/users/1/block", headers=as_user(1)).status_code == 400


# ---- account (ТЗ § 4.6, PIPA) ----

def test_export_contains_own_data(client):
    accept_terms(client, 1)
    approved(client, user=1)
    data = client.get("/api/me/export", headers=as_user(1)).json()
    assert data["profile"]["tg_id"] == "1"
    assert data["listings"][0]["title"] == ITEM["title"]


def test_delete_hides_listings_and_requires_new_consent(client):
    accept_terms(client, 1)
    lid = approved(client, user=1)
    assert client.post("/api/me/delete", headers=as_user(1)).status_code == 200
    assert all(x["id"] != lid for x in client.get("/api/listings").json())
    assert client.post("/api/listings", json=ITEM, headers=as_user(1)).status_code == 403


# ---- community and guide (ТЗ §6 and §7) ----

def test_groups_are_seeded(client):
    names = [g["name"] for g in client.get("/api/groups").json()]
    assert "Русскоговорящие в Корее" in names
    assert len(names) == 7


def test_unpublished_articles_are_hidden(client):
    assert client.get("/api/articles").json() == []
    articles = client.get("/api/articles", params={"section": "visas"}).json()
    assert articles == []


def test_publish_article_needs_source_and_date(client):
    body = {"title": "Регистрация иностранца", "body": "Текст", "source_url": "", "published": True}
    assert client.put("/api/admin/articles/1", json=body, headers=ADMIN).status_code == 400
    body.update({"source_url": "https://www.hikorea.go.kr", "checked_at": "2026-10-01T00:00:00Z"})
    assert client.put("/api/admin/articles/1", json=body, headers=ADMIN).status_code == 200
    listed = client.get("/api/articles").json()
    assert [a["title"] for a in listed] == ["Регистрация иностранца"]
    assert client.get("/api/articles/1").json()["source_url"] == "https://www.hikorea.go.kr"


def test_group_link_must_be_telegram(client):
    r = client.put("/api/admin/groups/1?telegram_link=https://evil.example", headers=ADMIN)
    assert r.status_code == 400
    r = client.put("/api/admin/groups/1?telegram_link=https://t.me/korea_ru", headers=ADMIN)
    assert r.status_code == 200
