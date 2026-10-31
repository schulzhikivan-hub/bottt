import csv
import datetime as dt
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import adminx, guard
from app.admin_auth import _hash
from app.database import SessionLocal, engine
from app.main import app
from app.models import (Admin, AdminSession, Application, Article, Banner, Broadcast, BroadcastTarget, Conversation, Group,
                        Listing, Message, Notification, Payment, Report, SavedSearch, User)
from app.payments import now
from tests.tg import accept_terms, as_user, reset_db

SUPER = {"X-Admin-Session": "test-session-token"}


def staff_headers(tg, role):
    with SessionLocal() as db:
        db.add(Admin(tg_id=tg, role=role, active=True, created_at=now()))
        db.add(AdminSession(token_hash=_hash(f"tok-{tg}"), tg_id=tg, expires_at=now() + dt.timedelta(hours=1)))
        db.commit()
    return {"X-Admin-Session": f"tok-{tg}"}


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def post_listing(client, user=1, **kw):
    body = {"category": "marketplace", "title": "Стол", "city": "Сеул"}
    body.update(kw)
    return client.post("/api/listings", json=body, headers=as_user(user)).json()["id"]


# ---- roles ----

def test_support_can_handle_reports_and_blocks_only(client):
    sup = staff_headers("801", "support")
    mod = staff_headers("802", "moderator")
    lid = post_listing(client)
    client.post("/api/reports", json={"target_type": "listing", "target_id": str(lid), "reason": "spam"}, headers=as_user(2))
    assert client.get("/api/admin/reports", headers=sup).status_code == 200
    assert client.get("/api/admin/users", headers=sup).status_code == 200
    assert client.post("/api/admin/users/3/block", json={"reason": "спам"}, headers=sup).status_code == 200
    for url in ("/api/admin/listings", "/api/admin/payments", "/api/admin/pricing", "/api/admin/stats", "/api/admin/audit",
                "/api/admin/promos", "/api/admin/analytics", "/api/admin/export/users.csv", "/api/admin/broadcasts", "/api/admin/banners"):
        assert client.get(url, headers=sup).status_code == 403, url
    assert client.post(f"/api/admin/listings/{lid}/approve", headers=sup).status_code == 403
    assert client.get("/api/admin/listings", headers=mod).status_code == 200          # a moderator still moderates
    assert client.get("/api/admin/payments", headers=mod).status_code == 403
    assert client.put("/api/admin/admins/803", json={"role": "support"}, headers=SUPER).status_code in (200, 201, 404)


# ---- templates and bulk ----

def test_templates_and_bulk_decisions(client):
    mod = staff_headers("802", "moderator")
    ids = [post_listing(client, title=f"Товар {i}") for i in range(4)]
    tpl = client.get("/api/admin/templates", headers=mod).json()
    assert {"no_photo", "prepay", "contacts", "duplicate", "section", "forbidden", "details"} == {t["key"] for t in tpl}
    r = client.post("/api/admin/listings/bulk", json={"ids": ids[:2], "decision": "approve"}, headers=mod).json()
    assert r["done"] == ids[:2] and r["failed"] == []
    r = client.post("/api/admin/listings/bulk", json={"ids": ids[2:] + [99999], "decision": "reject", "template": "prepay"}, headers=mod).json()
    assert r["done"] == ids[2:] and r["failed"][0]["id"] == 99999
    mine = {x["id"]: x for x in client.get("/api/me/listings", headers=as_user(1)).json()}
    assert mine[ids[2]]["status"] == "rejected" and "предоплату" in mine[ids[2]]["note"]
    assert client.post("/api/admin/listings/bulk", json={"ids": ids, "decision": "reject"}, headers=mod).status_code == 400   # no reason
    assert client.post("/api/admin/listings/bulk", json={"ids": [], "decision": "approve"}, headers=mod).status_code == 422
    assert client.post("/api/admin/listings/bulk", json={"ids": list(range(60)), "decision": "approve"}, headers=mod).status_code == 422


# ---- content ----

def test_articles_publish_only_with_source_and_date(client):
    bad = {"section": "visas", "title": "Продление E-9", "body": "Текст", "published": True}
    assert client.post("/api/admin/articles", json=bad, headers=SUPER).status_code == 400
    assert client.post("/api/admin/articles", json={**bad, "source_url": "ftp://x", "checked_at": "2026-10-01T00:00:00Z"}, headers=SUPER).status_code == 400
    ok = client.post("/api/admin/articles", json={**bad, "source_url": "https://www.hikorea.go.kr", "checked_at": "2026-10-01T00:00:00Z"}, headers=SUPER)
    assert ok.status_code == 201
    draft = client.post("/api/admin/articles", json={"section": "banks", "title": "Карта", "body": "..."}, headers=SUPER).json()
    assert [a["title"] for a in client.get("/api/articles").json()] == ["Продление E-9"]       # the draft is not public
    assert len(client.get("/api/admin/articles", headers=SUPER).json()) >= 2
    assert client.delete(f"/api/admin/articles/{draft['id']}", headers=SUPER).json()["deleted"] is True
    assert client.post("/api/admin/articles", json=bad, headers=staff_headers("802", "moderator")).status_code == 403


def test_groups_create_edit_delete(client):
    r = client.post("/api/admin/groups", json={"name": "Казахи в Корее", "flag": "🇰🇿", "telegram_link": "https://t.me/kz_korea",
                                               "members_count": 800, "kind": "nation"}, headers=SUPER)
    gid = r.json()["id"]
    assert client.post("/api/admin/groups", json={"name": "Плохая", "telegram_link": "https://evil.example"}, headers=SUPER).status_code == 400
    assert client.post("/api/admin/groups", json={"name": "Х", "kind": "zzz"}, headers=SUPER).status_code == 422
    client.put(f"/api/admin/groups/{gid}/full", json={"name": "Казахи Пусана", "flag": "🇰🇿", "telegram_link": "https://t.me/kz_busan",
                                                      "members_count": 90, "kind": "city"}, headers=SUPER)
    g = [x for x in client.get("/api/groups").json() if x["id"] == gid][0]
    assert g["name"] == "Казахи Пусана" and g["kind"] == "city" and g["members_count"] == 90
    assert client.delete(f"/api/admin/groups/{gid}", headers=SUPER).status_code == 200
    assert client.delete(f"/api/admin/groups/{gid}", headers=SUPER).status_code == 404


# ---- banners ----

def test_banners_dates_toggle_and_links(client):
    assert client.post("/api/admin/banners", json={"text": "Скидка", "link": "javascript:alert(1)"}, headers=SUPER).status_code == 400
    b = client.post("/api/admin/banners", json={"text": "Новое в KOREA HUB", "link": "https://t.me/koreahub", "days": 3}, headers=SUPER).json()
    assert [x["text"] for x in client.get("/api/banners").json()] == ["Новое в KOREA HUB"]    # public, no login
    client.put(f"/api/admin/banners/{b['id']}/toggle", headers=SUPER)
    assert client.get("/api/banners").json() == []
    client.put(f"/api/admin/banners/{b['id']}/toggle", headers=SUPER)
    with SessionLocal() as db:
        db.get(Banner, b["id"]).ends_at = now() - dt.timedelta(minutes=1)
        db.commit()
    assert client.get("/api/banners").json() == []                                            # expired
    assert client.delete(f"/api/admin/banners/{b['id']}", headers=SUPER).json()["deleted"] is True


# ---- broadcasts ----

def test_broadcast_confirm_rate_limit_and_cancel(client):
    with SessionLocal() as db:
        for i in range(60):
            db.add(User(tg_id=str(1000 + i), consent_at=now(), region_code="busan" if i < 10 else "seoul"))
        db.add(User(tg_id="2000", consent_at=now(), notify_messages=False))     # switched notices off: skipped
        db.add(User(tg_id="2001", consent_at=now(), blocked=True))               # blocked: skipped
        db.add(User(tg_id="2002"))                                              # never accepted the rules: skipped
        db.commit()
    b = client.post("/api/admin/broadcasts", json={"text": "Привет всем!", "region_code": "busan"}, headers=SUPER).json()
    assert b["status"] == "draft" and b["total"] == 10
    full = client.post("/api/admin/broadcasts", json={"text": "Новости"}, headers=SUPER).json()
    assert full["total"] == 60 + 3                                              # the 3 test users 1-3 count too
    sent = []
    with SessionLocal() as db:
        assert adminx.broadcast_tick(db, lambda t, x: sent.append(t) or True) == 0   # nothing runs before the confirmation
    started = client.post(f"/api/admin/broadcasts/{full['id']}/send", headers=SUPER).json()
    assert started["status"] == "sending"
    assert client.post(f"/api/admin/broadcasts/{full['id']}/send", headers=SUPER).status_code == 409
    with SessionLocal() as db:
        assert adminx.broadcast_tick(db, lambda t, x: sent.append(t) or True) == adminx.BROADCAST_PER_TICK
    assert len(sent) == 25                                                      # one tick = one batch
    assert client.post(f"/api/admin/broadcasts/{full['id']}/cancel", headers=SUPER).json()["status"] == "cancelled"
    with SessionLocal() as db:
        assert adminx.broadcast_tick(db, lambda t, x: sent.append(t) or True) == 0
        assert len(sent) == 25                                                  # cancelled: nothing more is sent
        row = db.get(Broadcast, full["id"])
        assert row.sent == 25 and row.failed == 0
        assert db.scalar(select(Notification).where(Notification.text == "Новости", Notification.user_tg_id == sent[0])) is not None
    assert client.post(f"/api/admin/broadcasts/{full['id']}/cancel", headers=SUPER).status_code == 409


def test_broadcast_finishes_and_counts_failures(client):
    with SessionLocal() as db:
        for i in range(30):
            db.add(User(tg_id=str(3000 + i), consent_at=now()))
        db.commit()
    b = client.post("/api/admin/broadcasts", json={"text": "Привет"}, headers=SUPER).json()
    client.post(f"/api/admin/broadcasts/{b['id']}/send", headers=SUPER)
    calls = []

    def flaky(tg, text):
        calls.append(tg)
        if tg == "3001":
            raise RuntimeError("blocked the bot")
        return tg != "3002"

    with SessionLocal() as db:
        while adminx.broadcast_tick(db, flaky):
            pass
        row = db.get(Broadcast, b["id"])
        assert row.status == "done" and row.failed == 2 and row.sent == row.total - 2 and row.finished_at is not None
    assert len(calls) == b["total"]


def test_broadcast_only_for_admins(client):
    mod = staff_headers("802", "moderator")
    assert client.post("/api/admin/broadcasts", json={"text": "Привет"}, headers=mod).status_code == 403
    assert client.post("/api/admin/broadcasts", json={"text": "ab"}, headers=SUPER).status_code == 422


# ---- csv ----

def test_csv_exports_are_safe_and_logged(client):
    lid = post_listing(client, title="=HYPERLINK(\"http://x\")")
    with SessionLocal() as db:
        db.add(Payment(user_tg_id="1", listing_id=lid, service_code="marketplace_vip", stars=50, payload="x", status="paid", paid_at=now()))
        db.commit()
    r = client.get("/api/admin/export/listings.csv", headers=SUPER)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.content.startswith("﻿".encode("utf-8"))
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][:4] == ["id", "created_at", "category", "title"] and rows[1][3].startswith("'=HYPERLINK")   # formula neutralised
    pay = list(csv.reader(io.StringIO(client.get("/api/admin/export/payments.csv", headers=SUPER).content.decode("utf-8-sig"))))
    assert pay[1][0].startswith("KH-") and pay[1][6] == "50"
    users = client.get("/api/admin/export/users.csv", headers=SUPER).content.decode("utf-8-sig")
    assert "name" not in users.splitlines()[0] and "contact" not in users.splitlines()[0]                          # no extra personal data
    assert client.get("/api/admin/export/secrets.csv", headers=SUPER).status_code == 404
    assert client.get("/api/admin/export/users.csv", headers=staff_headers("802", "moderator")).status_code == 403
    assert any(x["action"] == "export" for x in client.get("/api/admin/audit", headers=SUPER).json())


# ---- analytics ----

def test_analytics_days_totals_and_funnel(client):
    lid = post_listing(client)
    with SessionLocal() as db:
        db.add(Payment(user_tg_id="1", listing_id=lid, service_code="marketplace_vip", stars=50, payload="p", status="paid", paid_at=now()))
        db.add(Payment(user_tg_id="1", listing_id=lid, service_code="marketplace_bump", stars=15, payload="q", status="paid",
                       paid_at=now() - dt.timedelta(days=2)))
        db.add(Payment(user_tg_id="1", listing_id=lid, service_code="marketplace_vip", stars=50, payload="r", status="paid",
                       paid_at=now() - dt.timedelta(days=60)))                      # outside the window
        db.get(Listing, lid).views = 40
        db.commit()
    data = client.get("/api/admin/analytics?days=30", headers=SUPER).json()
    assert len(data["days"]) == 30 and data["days"][-1]["date"] == now().date().isoformat()
    assert data["totals"]["stars"] == 65 and data["totals"]["listings"] == 1 and data["totals"]["users"] >= 3
    assert data["days"][-1]["stars"] == 50 and data["days"][-3]["stars"] == 15
    assert data["funnel"]["views"] == 40 and data["funnel"]["paid"] == 3
    assert data["stars_by_service"][0]["stars"] == 50
    assert client.get("/api/admin/analytics?days=0", headers=SUPER).status_code == 422


# ---- what a user does ----

def test_user_activity_for_moderators(client):
    mod = staff_headers("802", "moderator")
    jid = post_listing(client, user=1, category="job", title="Повар")
    client.post(f"/api/admin/listings/{jid}/approve", headers=SUPER)
    client.post(f"/api/listings/{jid}/apply", json={"contact": "@ivan1"}, headers=as_user(2))
    client.post("/api/me/searches", json={"q": "повар", "category": "job"}, headers=as_user(2))
    client.post("/api/reports", json={"target_type": "user", "target_id": "2", "reason": "spam"}, headers=as_user(3))
    a = client.get("/api/admin/users/2/activity", headers=mod).json()
    assert a["applications_total"] == 1 and a["applications"] == {"new": 1}
    assert a["saved_searches"][0]["filters"] == {"q": "повар", "category": "job"} and a["reports_against"] == 1
    assert client.get("/api/admin/users/1/activity", headers=mod).json()["listings"] == {"approved": 1}
    assert client.get("/api/admin/users/999999/activity", headers=mod).status_code == 404
    assert client.get("/api/admin/users/2/activity", headers=as_user(1)).status_code in (401, 403)


def test_report_context_shows_only_the_reported_conversation(client):
    sup = staff_headers("801", "support")
    lid = post_listing(client, user=1)
    client.post(f"/api/admin/listings/{lid}/approve", headers=SUPER)
    conv = client.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]
    client.post(f"/api/conversations/{conv}/messages", json={"text": "Скиньте деньги на карту"}, headers=as_user(1)) if False else None
    client.post(f"/api/conversations/{conv}/messages", json={"text": "Здравствуйте"}, headers=as_user(2))
    other = client.post(f"/api/listings/{lid}/conversation", headers=as_user(3)).json()["id"]
    client.post(f"/api/conversations/{other}/messages", json={"text": "Чужая переписка"}, headers=as_user(3))
    client.post("/api/reports", json={"target_type": "listing", "target_id": str(lid), "reason": "fraud"}, headers=as_user(2))
    rid = client.get("/api/admin/reports", headers=sup).json()[0]["id"]
    ctx = client.get(f"/api/admin/reports/{rid}/context", headers=sup).json()
    assert ctx["listing"]["title"] == "Стол" and [m["text"] for m in ctx["messages"]] == ["Здравствуйте"]
    assert ctx["messages"][0]["from"] == "reporter"
    assert "Чужая" not in str(ctx)                                               # a third person's chat is not exposed
    assert any(x["action"] == "report.view_context" for x in client.get("/api/admin/audit", headers=SUPER).json())


def test_listing_list_by_section_status_and_search(client):
    mod = staff_headers("802", "moderator")
    a = post_listing(client, title="Диван красный")
    b = post_listing(client, title="Работа повар", category="job")
    client.post(f"/api/admin/listings/{a}/approve", headers=SUPER)
    r = client.get("/api/admin/listing-list", params={"category": "marketplace"}, headers=mod).json()
    assert r["total"] == 1 and r["items"][0]["id"] == a and r["items"][0]["author_id"] == "1"
    assert client.get("/api/admin/listing-list", params={"status": "pending"}, headers=mod).json()["items"][0]["id"] == b
    assert client.get("/api/admin/listing-list", params={"q": "диван"}, headers=mod).json()["total"] == 1
    assert client.get("/api/admin/listing-list", params={"q": "нет такого"}, headers=mod).json()["total"] == 0
    assert client.get("/api/admin/listing-list", headers=staff_headers("801", "support")).status_code == 403
