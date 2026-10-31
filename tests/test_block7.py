import io
import logging

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app import adminx, guard, observe
from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import AuditLog, Heartbeat, Listing
from app.payments import now
from tests.tg import accept_terms, as_user, reset_db

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    assert engine.url.drivername == "sqlite"
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def jpeg(size=(160, 120), color="red"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def make(client, user=1, **kw):
    body = {"category": "marketplace", "title": "Стол", "city": "Сеул", "description": "Хороший стол, самовывоз из Сеула, торг"}
    body.update(kw)
    r = client.post("/api/listings", json=body, headers=as_user(user))
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---- health ----

def test_health_reports_db_disk_and_bot(client):
    r = client.get("/health").json()
    assert r["status"] == "ok" and r["db"] is True and r["bot"] == "unknown" and r["disk_free_mb"] is not None
    with SessionLocal() as db:
        observe.beat(db, "bot")
    assert client.get("/health").json()["bot"] == "ok"
    import datetime as dt
    with SessionLocal() as db:
        db.get(Heartbeat, "bot").at = now() - dt.timedelta(minutes=10)
        db.commit()
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["bot"] == "stale"      # a silent bot is reported, but the site itself is fine
    assert client.get("/health/extended").json()["cert_days"] is None  # local run: no certificate to read


def test_health_fails_when_the_database_is_down(client, monkeypatch):
    class Broken:
        def __enter__(self):
            raise RuntimeError("db down")
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(observe, "SessionLocal", lambda: Broken())
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "error"


# ---- headers, request id, logs ----

def test_security_headers_and_request_id(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and "Referrer-Policy" in r.headers
    assert "frame-ancestors" in r.headers["Content-Security-Policy"] and "telegram.org" in r.headers["Content-Security-Policy"]
    assert "X-Frame-Options" not in r.headers                      # would break the Mini App inside Telegram
    assert len(r.headers["X-Request-ID"]) == 12
    assert client.get("/health").headers["X-Request-ID"] != r.headers["X-Request-ID"]
    assert "Strict-Transport-Security" not in r.headers
    assert "max-age" in client.get("/health", headers={"x-forwarded-proto": "https"}).headers["Strict-Transport-Security"]
    assert client.get("/api/admin/me", headers=ADMIN).headers["Cache-Control"] == "no-store"


def test_mask_path_hides_tokens():
    assert observe.mask_path("/api/auth/poll/" + "a1b2c3d4e5f6g7h8i9j0k1l2") == "/api/auth/poll/*"
    assert observe.mask_path("/api/listings/12/photos") == "/api/listings/12/photos"
    assert observe.mask_path("/api/catalog") == "/api/catalog"


def test_log_lines_are_json_and_hold_no_secrets(client):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(observe.JsonFormatter())
    observe.log.addHandler(handler)
    try:
        client.get("/api/catalog?q=секретное-слово", headers={"X-Telegram-Init-Data": "hash=SECRETHASH"})
    finally:
        observe.log.removeHandler(handler)
    import json
    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert line["msg"] == "request" and line["path"] == "/api/catalog" and line["status"] == 200 and "rid" in line
    assert "секретное" not in stream.getvalue() and "SECRETHASH" not in stream.getvalue()   # neither query nor headers


def test_server_error_is_caught_logged_and_alerted_once(client, monkeypatch):
    sent = []
    monkeypatch.setattr(observe.settings, "notifications_enabled", True)
    monkeypatch.setattr(observe.settings, "admin_ids", "999,888")
    monkeypatch.setattr(guard, "send_telegram", lambda tg, text: sent.append((tg, text)))
    observe._last_alert["at"] = 0.0

    @app.get("/api/_boom")
    def boom():
        raise RuntimeError("secret detail that must not leak")

    r = client.get("/api/_boom")
    assert r.status_code == 500 and "Номер для поддержки" in r.json()["detail"] and "secret" not in r.text
    assert [t[0] for t in sent] == ["999", "888"] and "_boom" in sent[0][1]
    client.get("/api/_boom")
    assert len(sent) == 2                                        # the second error within ten minutes is not announced again
    observe._last_alert["at"] = 0.0 if False else observe._last_alert["at"] - 601
    client.get("/api/_boom")
    assert len(sent) == 4                                        # after ten minutes the next one is


# ---- anti-abuse ----

def test_new_account_publishes_one_listing_per_hour(client, monkeypatch):
    monkeypatch.setattr(settings, "new_account_minutes", 10)
    assert client.post("/api/listings", json={"category": "marketplace", "title": "Первое", "city": "Сеул"}, headers=as_user(1)).status_code == 201
    r = client.post("/api/listings", json={"category": "marketplace", "title": "Второе", "city": "Сеул"}, headers=as_user(1))
    assert r.status_code == 429 and "Новый аккаунт" in r.json()["detail"]
    assert client.post("/api/listings", json={"category": "marketplace", "title": "Чужое", "city": "Сеул"}, headers=as_user(2)).status_code == 201
    import datetime as dt
    from app.models import User
    with SessionLocal() as db:
        db.get(User, "1").created_at = now() - dt.timedelta(minutes=30)       # an older account is not limited
        db.commit()
    assert client.post("/api/listings", json={"category": "marketplace", "title": "Третье", "city": "Сеул"}, headers=as_user(1)).status_code == 201


# ---- same text, same photo ----

def test_same_text_from_three_accounts_is_flagged_for_moderators(client):
    text = "Продаю диван, самовывоз, звоните в любое время суток, цена окончательная"
    a = make(client, 1, title="Диван один", description=text)
    make(client, 2, title="Диван два", description=text)
    c = make(client, 3, title="Диван три", description=text)
    flags = [x for x in client.get("/api/admin/listings", headers=ADMIN).json() if x["id"] == c][0]["flags"]
    assert any("3 разных аккаунтов" in f for f in flags)
    assert client.get(f"/api/admin/listings/{a}", headers=ADMIN).json()["flags"] is not None


def test_same_photo_under_two_accounts_is_flagged_but_own_reuse_is_fine(client):
    a = make(client, 1)
    b = make(client, 2, title="Другой стол", description="Совсем другое описание объявления про стол")
    c = make(client, 1, title="Стул", description="Стул, почти новый, из квартиры в Сеуле")
    pic = jpeg()
    up = lambda lid, user: client.post(f"/api/listings/{lid}/photos", files={"file": ("a.jpg", pic, "image/jpeg")}, headers=as_user(user))
    assert up(a, 1).status_code == 201
    assert up(c, 1).status_code == 201                             # the same author may reuse a photo: no flag
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "photo.duplicate_suspected")) is None
    assert up(b, 2).status_code == 201                             # another account, identical picture
    with SessionLocal() as db:
        row = db.scalar(select(AuditLog).where(AuditLog.action == "photo.duplicate_suspected"))
        assert row.object_id == str(b) and "#" in row.details
    flags = client.get(f"/api/admin/listings/{b}", headers=ADMIN).json()["flags"]
    assert any("то же фото" in f for f in flags)
    assert client.get(f"/api/admin/listings/{a}", headers=ADMIN).json()["flags"] == []


def test_photo_shape_rules(client):
    lid = make(client)
    up = lambda data: client.post(f"/api/listings/{lid}/photos", files={"file": ("a.jpg", data, "image/jpeg")}, headers=as_user(1))
    assert up(jpeg((60, 60))).status_code == 400 and "слишком маленькое" in up(jpeg((60, 60))).json()["detail"]
    assert up(jpeg((900, 100))).status_code == 400 and "пропорции" in up(jpeg((900, 100))).json()["detail"]
    assert up(jpeg((80, 80))).status_code == 201
    assert up(jpeg((400, 100))).status_code == 201                  # 4:1 is still allowed
