import datetime as dt

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Listing, User
from app.payments import now
from tests.tg import accept_terms, as_user, reset_db, TEST_ADMIN_SESSION

ADMIN = {"X-Admin-Session": TEST_ADMIN_SESSION}


def world():
    reset_db()
    c = TestClient(app)
    for u in (1, 2):
        accept_terms(c, u)
    return c


def listing(author="1", status="approved", **kw):
    with SessionLocal() as db:
        row = Listing(author_telegram_id=author, category="marketplace", title="Диван", description="x" * 40, city="Сеул",
                      status=status, contact="c", expires_at=now() + dt.timedelta(days=5), **kw)
        db.add(row); db.commit()
        return row.id


def test_support_ticket_roundtrip_without_any_nick():
    c = world()
    t = c.post("/api/tickets", json={"text": "Оплатил, но услуга не включилась"}, headers=as_user(1))
    assert t.status_code == 201
    tid = t.json()["id"]
    assert c.get("/api/tickets", headers=as_user(2)).json() == []                      # others do not see it
    assert c.get(f"/api/tickets/{tid}", headers=as_user(2)).status_code == 404
    assert c.get("/api/admin/tickets", headers=ADMIN).json()[0]["id"] == tid
    assert c.post(f"/api/admin/tickets/{tid}/reply", json={"text": "Проверили, включили"}, headers=ADMIN).json()["status"] == "answered"
    got = c.get(f"/api/tickets/{tid}", headers=as_user(1)).json()
    assert [m["staff"] for m in got["messages"]] == [False, True] and got["status"] == "answered"
    feed = c.get("/api/me/notifications", headers=as_user(1)).json()
    assert any("Поддержка ответила" in n["text"] for n in feed["items"])
    c.post(f"/api/tickets/{tid}/messages", json={"text": "Спасибо, работает"}, headers=as_user(1))
    assert c.get(f"/api/tickets/{tid}", headers=as_user(1)).json()["status"] == "open"   # a new message reopens
    assert c.post(f"/api/admin/tickets/{tid}/close", headers=ADMIN).json()["status"] == "closed"


def test_appeal_only_for_own_rejected_listing_and_restore():
    c = world()
    ok_id, bad_id = listing("1", "approved"), listing("1", "rejected", note="не то")
    assert c.post("/api/tickets", json={"text": "Прошу пересмотреть", "kind": "appeal", "listing_id": ok_id}, headers=as_user(1)).status_code == 400
    assert c.post("/api/tickets", json={"text": "Прошу пересмотреть", "kind": "appeal", "listing_id": bad_id}, headers=as_user(2)).status_code == 400
    a = c.post("/api/tickets", json={"text": "Прошу пересмотреть", "kind": "appeal", "listing_id": bad_id}, headers=as_user(1))
    assert a.status_code == 201
    assert c.post("/api/tickets", json={"text": "Ещё раз прошу", "kind": "appeal", "listing_id": bad_id}, headers=as_user(1)).status_code == 409
    assert c.get(f"/api/admin/tickets/{a.json()['id']}", headers=ADMIN).json()["listing"]["status"] == "rejected"
    r = c.post(f"/api/admin/tickets/{a.json()['id']}/restore-listing", headers=ADMIN)
    assert r.json()["status"] == "pending"          # back to moderation, not straight to the site


def test_settings_faq_and_rates_are_edited_in_admin():
    c = world()
    assert "Добро пожаловать" in c.get("/api/app-texts").json()["welcome_title"]
    c.put("/api/admin/app-settings", json={"values": {"welcome_title": "Привет!", "krw_rub": "0,065"}}, headers=ADMIN)
    assert c.get("/api/app-texts").json()["welcome_title"] == "Привет!"
    from app import main
    main._config_cache.clear()
    assert c.get("/api/config").json()["rates"] == {"rub": 0.065}
    assert c.put("/api/admin/app-settings", json={"values": {"nope": "1"}}, headers=ADMIN).status_code == 400
    assert c.put("/api/admin/app-settings", json={"values": {"krw_rub": "abc"}}, headers=ADMIN).status_code == 400
    f = c.post("/api/admin/faq", json={"question": "Как продлить?", "answer": "Кнопка «Продлить»"}, headers=ADMIN).json()["id"]
    assert c.get("/api/faq").json()[0]["question"] == "Как продлить?"
    c.put(f"/api/admin/faq/{f}", json={"question": "Как продлить?", "answer": "x" * 5, "active": False}, headers=ADMIN)
    assert c.get("/api/faq").json() == []


def test_automod_is_off_by_default_and_only_for_proven_authors():
    c = world()
    body = {"category": "marketplace", "title": "Стол письменный", "city": "Сеул", "price": "50 000 ₩",
            "description": "Хорошее состояние, самовывоз", "contact": "@seller_one"}
    first = c.post("/api/listings", json=body, headers=as_user(1))
    assert first.status_code == 201 and first.json()["status"] == "pending"           # switched off
    c.put("/api/admin/app-settings", json={"values": {"automod_enabled": "1", "automod_min_published": "1"}}, headers=ADMIN)
    second = c.post("/api/listings", json={**body, "title": "Стул офисный", "description": "Удобный стул, почти новый"}, headers=as_user(1))
    assert second.json()["status"] == "pending"                                       # no published history yet
    with SessionLocal() as db:
        db.get(User, "1").trusted = True
        db.commit()
    third = c.post("/api/listings", json={**body, "title": "Шкаф большой", "description": "Большой шкаф с зеркалом, самовывоз"}, headers=as_user(1))
    assert third.json()["status"] == "approved"                                       # trusted author


def test_review_reply_once_and_staff_stats():
    c = world()
    lid = listing("1")
    from app.models import Review
    with SessionLocal() as db:
        db.add(Review(listing_id=lid, target_tg_id="1", reviewer_tg_id="2", rating=3, text="ок")); db.commit()
        rid = db.query(Review).one().id
    assert c.post(f"/api/reviews/{rid}/reply", json={"text": "Спасибо за отзыв"}, headers=as_user(2)).status_code == 404
    assert c.post(f"/api/reviews/{rid}/reply", json={"text": "Спасибо за отзыв"}, headers=as_user(1)).status_code == 200
    assert c.post(f"/api/reviews/{rid}/reply", json={"text": "Ещё раз"}, headers=as_user(1)).status_code == 409
    assert c.get(f"/api/authors/{lid}/reviews").json()["items"][0]["reply"] == "Спасибо за отзыв"
    assert isinstance(c.get("/api/admin/staff-stats", headers=ADMIN).json(), list)


def test_service_worker_and_pages_are_served():
    c = TestClient(app)
    r = c.get("/sw.js")
    assert r.status_code == 200 and r.headers["service-worker-allowed"] == "/" and "javascript" in r.headers["content-type"]
    html = c.get("/").text
    assert 'data-menu="support"' in html and 'id="welcomeSheet"' in html


def test_pages_ask_for_versioned_files_and_never_cache_stale_ones():
    from app import buildinfo
    c = TestClient(app)
    page = c.get("/")
    v = buildinfo.build_id()
    assert f'/static/app.js?v={v}' in page.text and f'/static/app.css?v={v}' in page.text and f'name="build" content="{v}"' in page.text
    assert page.headers["cache-control"] == "no-cache"
    assert c.get("/static/app.js").headers["cache-control"] == "no-cache"
    assert c.get("/static/app.css").headers["cache-control"] == "no-cache"
    assert c.get("/health").json()["build"] == v and c.get("/api/config").json()["build"] == v
    assert f"/static/admin.js?v={v}" in c.get("/admin").text


def test_telegram_buttons_carry_the_version():
    from app import buildinfo
    from app.config import settings
    url = buildinfo.app_url()
    assert url.startswith(settings.webapp_url.rstrip("/")) and url.endswith("?b=" + buildinfo.build_id())
    assert buildinfo.app_url("?section=jobs").endswith("?section=jobs&b=" + buildinfo.build_id())
