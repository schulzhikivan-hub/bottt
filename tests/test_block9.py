import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app import admin_ops as ops
from app import botadmin as ba
from app import guard
from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import Admin, DatingProfile, Notification
from app.payments import now
from tests.tg import accept_terms, as_user, reset_db

ADMIN = {"X-Admin-Session": "test-session-token"}
THIS_YEAR = now().year


@pytest.fixture()
def client(tmp_path, monkeypatch):
    assert engine.url.drivername == "sqlite"
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    reset_db()
    ba.PENDING_INPUT.clear()
    with TestClient(app) as c:
        for uid in (1, 2):
            accept_terms(c, uid)
        yield c


def jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (200, 160), "tan").save(buf, "JPEG")
    return buf.getvalue()


def make_profile(client, user=1):
    body = {"birth_year": THIS_YEAR - 27, "gender": "f", "region_code": "seoul", "bio": "Привет", "first_name": "Аня"}
    assert client.put("/api/dating/profile", json=body, headers=as_user(user)).status_code == 200
    r = client.post("/api/dating/photo", files={"file": ("p.jpg", jpeg(), "image/jpeg")}, headers=as_user(user))
    assert r.status_code == 200
    return client.get("/api/dating/me", headers=as_user(user)).json()["id"]


def selfie(client, user=1, data=None):
    return client.post("/api/dating/selfie", files={"file": ("s.jpg", data or jpeg(), "image/jpeg")}, headers=as_user(user))


def test_selfie_needs_a_profile_and_photo_and_a_real_image(client):
    assert selfie(client).status_code == 400                         # no profile yet
    make_profile(client)
    assert selfie(client, data=b"not a picture").status_code == 400
    r = selfie(client)
    assert r.status_code == 201 and r.json()["selfie_status"] == "pending"
    assert client.get("/api/dating/me", headers=as_user(1)).json()["selfie_status"] == "pending"


def test_selfie_is_visible_to_moderators_only_and_logged(client):
    pid = make_profile(client)
    selfie(client)
    assert client.get(f"/api/admin/dating/{pid}/selfie").status_code in (401, 403)         # nobody without a staff session
    assert client.get(f"/api/admin/dating/{pid}/selfie", headers=as_user(1)).status_code in (401, 403)
    r = client.get(f"/api/admin/dating/{pid}/selfie", headers=ADMIN)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert any(x["action"] == "dating.view_selfie" for x in client.get("/api/admin/audit", headers=ADMIN).json())
    client.post(f"/api/admin/dating/{pid}/approve", headers=ADMIN)                           # published
    assert "selfie" not in str(client.get("/api/dating", headers=as_user(2)).json())         # the public feed has no selfie


def test_verify_adds_mark_and_notifies_and_reject_removes_file(client, tmp_path):
    pid = make_profile(client)
    selfie(client)
    client.post(f"/api/admin/dating/{pid}/approve", headers=ADMIN)
    assert client.get("/api/dating", headers=as_user(2)).json()[0]["verified"] is False
    queue = client.get("/api/admin/dating?status=pending", headers=ADMIN).json()
    assert queue and queue[0]["has_selfie"] and queue[0]["selfie_status"] == "pending"        # public profile, selfie still waits
    assert client.post(f"/api/admin/dating/{pid}/verify", headers=ADMIN).status_code == 200
    assert client.get("/api/dating", headers=as_user(2)).json()[0]["verified"] is True
    assert client.get("/api/admin/dating?status=pending", headers=ADMIN).json() == []
    with SessionLocal() as db:
        assert any("проверено" in n.text for n in db.scalars(select(Notification).where(Notification.user_tg_id == "1")).all())
    # a new main photo asks for the confirmation again
    client.post("/api/dating/photo", files={"file": ("p.jpg", jpeg(), "image/jpeg")}, headers=as_user(1))
    assert client.get("/api/dating/me", headers=as_user(1)).json()["selfie_status"] == "pending"
    # rejecting deletes the file
    with SessionLocal() as db:
        key = db.get(DatingProfile, pid).selfie_key
    assert (tmp_path / "uploads" / key).exists()
    client.post(f"/api/admin/dating/{pid}/reject_selfie", headers=ADMIN)
    assert not (tmp_path / "uploads" / key).exists()
    with SessionLocal() as db:
        row = db.get(DatingProfile, pid)
        assert row.selfie_key is None and row.selfie_status == "rejected"
    assert client.post(f"/api/admin/dating/{pid}/verify", headers=ADMIN).status_code == 400   # nothing left to verify


def test_bot_admin_selfie_flow(client, tmp_path):
    with SessionLocal() as db:
        db.add(Admin(tg_id="555", role="moderator", active=True, created_at=now()))
        db.commit()
    pid = make_profile(client)
    selfie(client)
    with SessionLocal() as db:
        queue = ba.dating_queue(db)
        assert "Прислано фото-подтверждение" in queue.text and any(d == f"a:D:vs:{pid}" for r in queue.rows for _, d in r)
        shown = ba.handle(db, 555, "moderator", ["a", "D", "vs", str(pid)])
        assert shown.photo is not None and shown.photo.exists()
        done = ba.handle(db, 555, "moderator", ["a", "D", "vf", str(pid)])
        assert "Личность подтверждена" in done.text
        assert db.get(DatingProfile, pid).selfie_status == "approved"
        assert ba.handle(db, 555, "moderator", ["a", "D", "vs", "9999"]).alert == "Селфи не найдено"


def test_bot_browse_shows_verified_mark(client):
    from app import botbrowse as bb
    pid = make_profile(client)
    selfie(client)
    client.post(f"/api/admin/dating/{pid}/approve", headers=ADMIN)
    client.post(f"/api/admin/dating/{pid}/verify", headers=ADMIN)
    with SessionLocal() as db:
        bb.STATE.clear()
        assert "Аня, 27 ✔" in bb.act(db, 2, "b:dt").text


def test_stars_report_by_day_and_service(client):
    from app.models import Payment
    with SessionLocal() as db:
        db.add(Payment(user_tg_id="1", listing_id=1, service_code="job_vip", stars=100, payload="a", status="paid", paid_at=now()))
        db.add(Payment(user_tg_id="1", listing_id=1, service_code="job_bump", stars=30, payload="b", status="paid", paid_at=now()))
        db.commit()
    data = client.get("/api/admin/analytics?days=30", headers=ADMIN).json()
    today = data["days"][-1]
    assert today["payments"] == 2 and today["stars"] == 130
    assert {s["service"]: s["stars"] for s in data["stars_by_service"]} == {"VIP-вакансия на 7 дней": 100, "Поднять вакансию": 30}


def test_user_navigation_remains_zoomable_and_accessible(client):
    page = client.get("/").text
    assert "user-scalable=no" not in page
    assert '<nav class="bottom" aria-label="Основная навигация">' in page
    js = client.get("/static/app.js").text
    assert 'setAttribute("aria-current", "page")' in js


def test_banner_photo_and_language_button_are_wired_in_the_page(client):
    js = client.get("/static/app.js").text
    assert "/api/banner-photo" in js and 'classList.add("photo")' in js
    assert client.get("/api/banner-photo").json() == {"exists": False}
    assert "startI18n()" in js and "/static/i18n/${i18n.lang}.json" in js
    assert "English (beta)" in js and "한국어 (beta)" in js
    assert ".hero.photo" in client.get("/static/app.css").text


def test_rate_limit_is_shared_through_the_database():
    """A second worker (fresh in-memory state) still sees the first worker's hits."""
    from fastapi import HTTPException
    from app import guard
    reset_db()
    for _ in range(3):
        guard.rate_limit("u1", "shared", 3, 60)
    guard._hits.clear()                     # as if another process asks
    try:
        guard.rate_limit("u1", "shared", 3, 60)
        assert False, "must be limited"
    except HTTPException as e:
        assert e.status_code == 429
    guard.rate_limit("u2", "shared", 3, 60)  # other user is unaffected


def test_similar_listings_same_category_other_authors():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import SessionLocal
    from app.models import Listing
    from app.payments import now
    import datetime as dt
    reset_db()
    with SessionLocal() as db:
        def add(author, cat, city, status="approved"):
            row = Listing(author_telegram_id=author, category=cat, title=f"{cat}-{author}", description="x" * 40, city=city,
                          status=status, contact="c", expires_at=now() + dt.timedelta(days=5))
            db.add(row); db.commit(); return row.id
        base = add("1", "job", "Сеул")
        same = add("2", "job", "Сеул")
        add("3", "housing", "Сеул")           # other category
        add("1", "job", "Сеул")               # same author
        add("4", "job", "Сеул", "pending")    # not published
        c = TestClient(app)
        got = [r["id"] for r in c.get(f"/api/listings/{base}/similar").json()]
        assert got == [same]
        assert c.get("/api/listings/9999/similar").status_code == 404


def test_start_source_tag_is_first_touch_and_counted():
    from app import bot, adminx
    from app.database import SessionLocal
    reset_db()
    bot._remember_source("77", "ads_may")
    bot._remember_source("77", "other")        # second touch ignored
    bot._remember_source("78", "bad tag!")     # junk ignored
    with SessionLocal() as db:
        assert adminx.analytics(db, 30)["sources"] == [{"source": "ads_may", "users": 1}]


def test_section_and_city_pages_for_search_engines():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import SessionLocal
    from app.models import Listing
    from app.payments import now
    import datetime as dt
    reset_db()
    with SessionLocal() as db:
        row = Listing(author_telegram_id="1", category="job", title="Повар в Пусане", description="x" * 40, city="Пусан",
                      city_key="пусан", status="approved", contact="c", expires_at=now() + dt.timedelta(days=5))
        db.add(row); db.commit()
    c = TestClient(app)
    page = c.get("/c/job/Пусан")
    assert page.status_code == 200 and "Повар в Пусане" in page.text and 'content="index,follow"' in page.text
    empty = c.get("/c/housing/Сеул")
    assert empty.status_code == 200 and "noindex" in empty.text
    assert c.get("/c/nothing").status_code == 404
    sm = c.get("/sitemap.xml").text
    assert "/c/job" in sm and "/c/job/%D0%9F%D1%83%D1%81%D0%B0%D0%BD" in sm
