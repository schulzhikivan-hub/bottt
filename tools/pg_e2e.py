"""Checks the main flows on a REAL PostgreSQL (the automatic tests use SQLite). Not part of pytest.

    createdb koreahub_check
    DATABASE_URL=postgresql+psycopg://USER@HOST:PORT/koreahub_check python tools/pg_e2e.py

It creates test data, so use an empty throw-away database. Run it after changing models or queries."""
import io, os, sys, datetime as dt
sys.path.insert(0, "/home/user/bottt")
os.environ.update(BOT_TOKEN="123456:TEST-TOKEN", ADMIN_IDS="999", NOTIFICATIONS_ENABLED="false", UPLOAD_DIR="/tmp/pgup", NEW_ACCOUNT_MINUTES="0", REQUIRE_PHOTOS="false")
from fastapi.testclient import TestClient
from PIL import Image
from app.main import app
from app import payments, adminx
from app.database import SessionLocal, engine
from app.models import Admin, AdminSession, User
from app.admin_auth import _hash
from app.payments import now
from tests.tg import as_user, accept_terms
assert engine.dialect.name == "postgresql", engine.dialect.name
ok = lambda c, m: print(("ok   " if c else "FAIL ") + m) or (c or sys.exit(1))
payments.telegram_call = lambda method, payload: f"https://t.me/invoice/{payload['payload'][:8]}"
with SessionLocal() as db:
    if db.get(Admin, "999") is None: db.add(Admin(tg_id="999", role="super_admin", active=True, created_at=now()))
    db.add(AdminSession(token_hash=_hash("pgtok"), tg_id="999", expires_at=now() + dt.timedelta(hours=1))); db.commit()
A = {"X-Admin-Session": "pgtok"}
def img(size=(200, 150)):
    b = io.BytesIO(); Image.new("RGB", size, "teal").save(b, "JPEG"); return b.getvalue()
with TestClient(app) as c:
    for u in (1, 2, 3): accept_terms(c, u)
    jid = c.post("/api/listings", json={"category": "job", "title": "Повар в Сеуле", "city": "Сеул", "price_amount": 2500000, "visas": ["E-9"], "schedule": "day", "housing_provided": True, "details": {"company": "Samin"}}, headers=as_user(1)).json()["id"]
    hid = c.post("/api/listings", json={"category": "housing", "title": "Квартира Каннам", "city": "Сеул, Каннам", "price_amount": 900000, "housing_type": "studio", "deposit": 5000000}, headers=as_user(1)).json()["id"]
    cid = c.post("/api/listings", json={"category": "marketplace", "title": "Sonata 2018", "city": "Сеул", "goods_category": "auto", "details": {"year": 2018, "mileage_km": 85000, "model": "Sonata"}}, headers=as_user(1)).json()["id"]
    sid = c.post("/api/listings", json={"category": "marketplace", "title": "Перевод документов", "city": "Сеул", "goods_category": "services", "details": {"service_area": "Сеул"}}, headers=as_user(1)).json()["id"]
    ok(all([jid, hid, cid, sid]), "created 4 listings of every kind")
    ok(c.post(f"/api/listings/{hid}/photos", files={"file": ("a.jpg", img(), "image/jpeg")}, headers=as_user(1)).status_code == 201, "photo upload (hash column)")
    res = c.post("/api/admin/listings/bulk", json={"ids": [jid, hid, cid, sid], "decision": "approve"}, headers=A).json()
    ok(len(res["done"]) == 4, f"bulk approve {res}")
    cat = lambda **p: [x["id"] for x in c.get("/api/catalog", params=p).json()["items"]]
    ok(cat(category="job", visa="E-9") == [jid], "catalog visa filter (LIKE)")
    ok(cat(goods_category="auto", year_min=2015, mileage_max=100000) == [cid], "car filters year/mileage")
    ok(cat(q="повар") == [jid], "search words")
    ok(len(c.get("/api/suggest", params={"q": "перевод"}).json()) >= 1 and any(x["type"] == "place" for x in c.get("/api/suggest", params={"q": "каннам"}).json()), "suggestions (titles and places)")
    ok(c.get(f"/api/authors/{jid}").json()["card"]["active"] == 4, "author page")
    ok(c.post(f"/api/listings/{cid}/reserve", json={"on": True}, headers=as_user(1)).json()["reserved"] is True, "reserved mark")
    ok(c.post(f"/api/listings/{jid}/apply", json={"contact": "@ivan1", "message": "hi"}, headers=as_user(2)).status_code == 201, "apply")
    aid = c.get(f"/api/listings/{jid}/applications", headers=as_user(1)).json()[0]["id"]
    ok(c.put(f"/api/applications/{aid}", json={"status": "invited", "reply": "Приходите"}, headers=as_user(1)).status_code == 200, "employer reply")
    ok(c.post(f"/api/applications/{aid}/withdraw", headers=as_user(2)).status_code == 200, "withdraw")
    conv = c.post(f"/api/listings/{hid}/conversation", headers=as_user(2)).json()["id"]
    c.post(f"/api/conversations/{conv}/messages", json={"text": "Здравствуйте"}, headers=as_user(2))
    ok(c.get(f"/api/conversations/{conv}/messages", headers=as_user(1)).json()[0]["text"] == "Здравствуйте", "chat message")
    ok(c.get(f"/api/conversations/{conv}/messages", headers=as_user(2)).json()[0]["read"] is True, "read receipt")
    ok(c.post(f"/api/conversations/{conv}/photo", files={"file": ("p.jpg", img(), "image/jpeg")}, headers=as_user(2)).status_code == 201, "chat photo")
    ok(c.get("/api/me/notifications", headers=as_user(1)).json()["unread"] >= 1, "notification feed")
    c.put("/api/me/notify-prefs", json={"kinds": {"messages": False}}, headers=as_user(1))
    ok(c.get("/api/me/notify-prefs", headers=as_user(1)).json()["kinds"][0]["on"] is False, "notify prefs (JSON column)")
    ok(c.post("/api/me/searches", json={"q": "повар", "year_min": 2015}, headers=as_user(2)).status_code == 201, "saved search (JSON)")
    c.post("/api/admin/promos", json={"code": "spring", "percent": 20}, headers=A)
    inv = c.post("/api/payments/invoice", json={"listing_id": jid, "service_code": "job_vip", "promo_code": "SPRING"}, headers=as_user(1))
    ok(inv.status_code == 200 and inv.json()["stars"] == 80, "invoice with promo")
    with SessionLocal() as db:
        from app.models import Payment
        p = db.get(Payment, inv.json()["payment_id"])
        payments.process_successful_payment(db, "1", p.payload, "chg-1", "XTR", p.stars)
    ok(c.get(f"/api/me/payments/{inv.json()['payment_id']}/receipt", headers=as_user(1)).json()["discount"] == 20, "receipt")
    ok(len(c.get(f"/api/listings/{jid}/services", headers=as_user(1)).json()["options"]) >= 3, "services showcase")
    a = c.get("/api/admin/analytics?days=30", headers=A).json()
    ok(a["totals"]["stars"] == 80 and a["funnel"]["paid"] == 1, "analytics (dates on PostgreSQL)")
    ok(c.get("/api/admin/export/payments.csv", headers=A).status_code == 200 and c.get("/api/admin/export/users.csv", headers=A).status_code == 200, "csv exports")
    b = c.post("/api/admin/broadcasts", json={"text": "Привет всем"}, headers=A).json()
    c.post(f"/api/admin/broadcasts/{b['id']}/send", headers=A)
    with SessionLocal() as db:
        tried = adminx.broadcast_tick(db, lambda tg, t: True)
    ok(tried >= 1, f"broadcast tick ({tried})")
    c.post("/api/admin/banners", json={"text": "Новости", "days": 3}, headers=A)
    ok(len(c.get("/api/banners").json()) == 1, "banners")
    ok(c.get("/api/admin/listing-list", params={"category": "job", "q": "повар"}, headers=A).json()["total"] == 1, "admin list with search")
    ok(c.get("/api/admin/listings", headers=A).status_code == 200 and c.get(f"/api/admin/listings/{jid}", headers=A).json()["flags"] == [], "admin queue + flags")
    ok(c.get(f"/l/{jid}").status_code == 200 and "Повар" in c.get(f"/l/{jid}").text, "public listing page")
    ok(f"/l/{jid}" in c.get("/sitemap.xml").text, "sitemap")
    prof = {"birth_year": dt.date.today().year - 27, "gender": "f", "region_code": "seoul", "bio": "hi", "first_name": "Аня"}
    c.put("/api/dating/profile", json=prof, headers=as_user(2)); c.post("/api/dating/photo", files={"file": ("p.jpg", img(), "image/jpeg")}, headers=as_user(2))
    c.post("/api/dating/selfie", files={"file": ("s.jpg", img(), "image/jpeg")}, headers=as_user(2))
    pid = c.get("/api/dating/me", headers=as_user(2)).json()["id"]
    c.post(f"/api/admin/dating/{pid}/approve", headers=A); c.post(f"/api/admin/dating/{pid}/verify", headers=A)
    feed = c.get("/api/dating", params={"age_min": 20, "age_max": 30, "region": "seoul"}, headers=as_user(1)).json()
    ok(len(feed) == 1 and feed[0]["verified"] is True and feed[0]["name"] == "Аня", "dating feed with filters, name and verified")
    ok(c.get("/api/dating/likes-me", headers=as_user(2)).json()["count"] == 0, "likes-me")
    # block hides listings and unblock restores (UPDATE with note marker)
    c.post("/api/admin/users/1/block", json={"reason": "тест блокировки"}, headers=A)
    ok(c.get(f"/api/listings/{jid}").status_code == 404, "blocked user's listing hidden")
    c.post("/api/admin/users/1/unblock", json={"reason": "тест разблокировки"}, headers=A)
    ok(c.get(f"/api/listings/{jid}").status_code == 200, "unblock restores")
    h = c.get("/health/extended").json()
    ok(h["status"] == "ok" and h["db"] is True, f"health on PostgreSQL {h}")
    from app import lifecycle
    with SessionLocal() as db:
        lifecycle.expiring_soon(db); lifecycle.expiring_tomorrow(db); lifecycle.expire_listings(db); payments.placements_ending_soon(db); payments.expire_placements(db)
    ok(True, "expiry helpers run on PostgreSQL")
print("PG E2E DONE")
