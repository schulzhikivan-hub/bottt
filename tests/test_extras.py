import datetime as dt
import io

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import DatingProfile, Favorite, Listing, Review, User
from app.payments import now
from tests.tg import accept_terms, as_user, reset_db, TEST_ADMIN_SESSION

ADMIN = {"X-Admin-Session": TEST_ADMIN_SESSION}


def world():
    reset_db()
    c = TestClient(app)
    for u in (1, 2):
        accept_terms(c, u)
    with SessionLocal() as db:
        row = Listing(author_telegram_id="1", category="marketplace", title="Диван", description="x" * 40, city="Сеул",
                      status="approved", contact="c", price_amount=100000, expires_at=now() + dt.timedelta(days=5))
        db.add(row); db.commit()
        lid = row.id
    conv = c.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]
    return c, lid, conv


def test_batch_keeps_order_and_hides_unpublished():
    c, lid, _ = world()
    with SessionLocal() as db:
        hidden = Listing(author_telegram_id="1", category="job", title="Скрыто", description="x" * 40, city="Сеул", status="hidden", contact="c")
        db.add(hidden); db.commit(); hid = hidden.id
    got = c.get(f"/api/listings-batch?ids={hid},{lid},999").json()
    assert [x["id"] for x in got] == [lid]


def test_stats_and_my_reviews_with_tags():
    c, lid, conv = world()
    c.post(f"/api/conversations/{conv}/messages", json={"text": "Здравствуйте"}, headers=as_user(2))
    c.post(f"/api/conversations/{conv}/messages", json={"text": "Добрый день"}, headers=as_user(1))
    c.post(f"/api/favorites/{lid}", headers=as_user(2))
    st = c.get(f"/api/listings/{lid}/stats", headers=as_user(1)).json()
    assert st["favorites"] == 1 and st["chats"] == 1
    assert c.get(f"/api/listings/{lid}/stats", headers=as_user(2)).status_code == 404
    assert c.post("/api/reviews", json={"listing_id": lid, "rating": 5, "tags": ["polite", "hack", "fast"]}, headers=as_user(2)).status_code == 201
    mine = c.get("/api/me/reviews", headers=as_user(1)).json()
    assert mine["reviews"] == 1 and mine["items"][0]["tags"] == ["Вежливый", "Быстро отвечает"] and mine["items"][0]["can_reply"] is True


def test_price_drop_notifies_people_who_saved_it():
    c, lid, _ = world()
    c.post(f"/api/favorites/{lid}", headers=as_user(2))
    from app import lifecycle
    with SessionLocal() as db:
        item = db.get(Listing, lid)
        item.pending_changes = {"price_amount": 80000}
        lifecycle.apply_edit(db, item)
        db.commit()
    feed = c.get("/api/me/notifications", headers=as_user(2)).json()["items"]
    assert any("Цена снизилась" in n["text"] for n in feed)
    with SessionLocal() as db:       # a raise does not notify
        item = db.get(Listing, lid)
        item.pending_changes = {"price_amount": 90000}
        lifecycle.apply_edit(db, item)
        db.commit()
    assert sum("Цена снизилась" in n["text"] for n in c.get("/api/me/notifications", headers=as_user(2)).json()["items"]) == 1


def test_typing_marker():
    c, lid, conv = world()
    assert c.get(f"/api/conversations/{conv}/status", headers=as_user(1)).json()["typing"] is False
    assert c.post(f"/api/conversations/{conv}/typing", headers=as_user(2)).status_code == 200
    assert c.get(f"/api/conversations/{conv}/status", headers=as_user(1)).json()["typing"] is True
    assert c.get(f"/api/conversations/{conv}/status", headers=as_user(2)).json()["typing"] is False


def test_attachments_pdf_voice_and_access():
    c, lid, conv = world()
    pdf = b"%PDF-1.4\n" + b"0" * 100
    r = c.post(f"/api/conversations/{conv}/attachment", files={"file": ("Договор.pdf", pdf, "application/pdf")}, headers=as_user(2))
    assert r.status_code == 201 and r.json()["file"]["kind"] == "pdf"
    url = r.json()["file"]["url"]
    got = c.get(url, headers=as_user(1))
    assert got.status_code == 200 and got.headers["content-disposition"].startswith("attachment") and got.headers["x-content-type-options"] == "nosniff"
    accept_terms(c, 3)
    assert c.get(url, headers=as_user(3)).status_code in {403, 404}           # outsiders never get it
    assert c.get(url).status_code in {401, 403}
    exe = c.post(f"/api/conversations/{conv}/attachment", files={"file": ("a.pdf", b"MZ\x90\x00" + b"0" * 50, "application/pdf")}, headers=as_user(2))
    assert exe.status_code == 400                                           # judged by bytes, not by the name
    voice = c.post(f"/api/conversations/{conv}/attachment", files={"file": ("v.webm", b"\x1a\x45\xdf\xa3" + b"0" * 50, "audio/webm")}, headers=as_user(2))
    assert voice.json()["file"]["kind"] == "audio"
    big = c.post(f"/api/conversations/{conv}/attachment", files={"file": ("b.pdf", b"%PDF-" + b"0" * (5 * 1024 * 1024 + 10), "application/pdf")}, headers=as_user(2))
    assert big.status_code == 413
    msgs = c.get(f"/api/conversations/{conv}/messages", headers=as_user(1)).json()
    assert [m["file"]["kind"] for m in msgs if m.get("file")] == ["pdf", "audio"]


def test_wizard_draft_roundtrip_and_dating_pause_filters():
    c, lid, conv = world()
    assert c.get("/api/me/wizard-draft", headers=as_user(2)).json() is None
    c.put("/api/me/wizard-draft", json={"data": {"title": "Стол"}, "step": 2}, headers=as_user(2))
    assert c.get("/api/me/wizard-draft", headers=as_user(2)).json()["data"] == {"title": "Стол"}
    assert c.get("/api/me/wizard-draft", headers=as_user(1)).json() is None
    c.delete("/api/me/wizard-draft", headers=as_user(2))
    assert c.get("/api/me/wizard-draft", headers=as_user(2)).json() is None
    with SessionLocal() as db:
        db.add(DatingProfile(owner_tg_id="1", birth_year=1995, gender="f", city="Сеул", status="published", photo_key="x"))
        u = db.get(User, "1"); u.last_seen_at = now() - dt.timedelta(days=10)
        db.commit()
    assert len(c.get("/api/dating", headers=as_user(2)).json()) == 1
    assert c.get("/api/dating?active=true", headers=as_user(2)).json() == []          # not seen for 10 days
    assert c.post("/api/dating/pause", json={"on": True}, headers=as_user(1)).json() == {"paused": True}
    assert c.get("/api/dating", headers=as_user(2)).json() == []
    pid = c.get("/api/dating/me", headers=as_user(1)).json()["id"]
    c.post(f"/api/admin/dating/{pid}/reject?reason=Лицо+не+видно", headers=ADMIN)
    assert c.get("/api/dating/me", headers=as_user(1)).json()["note"] == "Лицо не видно"
