import datetime as dt

from fastapi.testclient import TestClient

from app import adminplus, adminx
from app.database import SessionLocal
from app.main import app
from app.models import Listing, Notification, Resume, Setting, User
from app.payments import now
from tests.tg import accept_terms, as_user, reset_db, TEST_ADMIN_SESSION

ADM = {"X-Admin-Session": TEST_ADMIN_SESSION}


def world():
    reset_db()
    c = TestClient(app)
    for u in (1, 2):
        accept_terms(c, u)
    with SessionLocal() as db:
        row = Listing(author_telegram_id="1", category="marketplace", title="Диван кожаный", description="Звоните 010 тест", city="Сеул",
                      status="pending", contact="c", price="100", expires_at=now() + dt.timedelta(days=5))
        db.add(row); db.commit()
        return c, row.id


def test_counters_and_staff_alerts_with_throttle():
    c, lid = world()
    c.post("/api/tickets", json={"text": "Помогите разобраться"}, headers=as_user(1))
    c.post("/api/tickets", json={"text": "Ещё одно обращение"}, headers=as_user(2))
    cnt = c.get("/api/admin/counters", headers=ADM).json()
    assert cnt["listings"] == 1 and cnt["support"] == 2 and cnt["reports"] == 0
    with SessionLocal() as db:
        got = db.query(Notification).filter(Notification.user_tg_id == "999", Notification.text.like("%обращение%")).count()
    assert got == 1                                    # two tickets within 10 minutes: one alert


def test_global_search_user_card_notes_and_edit():
    c, lid = world()
    res = c.get(f"/api/admin/search?q={lid}", headers=ADM).json()
    assert res["listings"][0]["id"] == lid
    assert c.get("/api/admin/search?q=диван", headers=ADM).json()["listings"][0]["id"] == lid
    assert c.get("/api/admin/search?q=1", headers=ADM).json()["users"][0]["tg_id"] == "1"
    card = c.get("/api/admin/users/1/card", headers=ADM).json()
    assert card["listings"][0]["id"] == lid and card["notes"] == []
    c.post("/api/admin/users/1/notes", json={"text": "Предупреждён о телефонах в описании"}, headers=ADM)
    assert c.get("/api/admin/users/1/card", headers=ADM).json()["notes"][0]["text"].startswith("Предупреждён")
    ed = c.put(f"/api/admin/listings/{lid}/edit", json={"description": "Кожаный диван, самовывоз"}, headers=ADM)
    assert ed.json() == {"changed": ["description"]}
    assert c.put(f"/api/admin/listings/{lid}/edit", json={"description": "Кожаный диван, самовывоз"}, headers=ADM).json() == {"changed": []}
    feed = c.get("/api/me/notifications", headers=as_user(1)).json()["items"]
    assert any("поправил" in n["text"] for n in feed)


def test_claim_blocks_second_person_and_undo_works_once():
    c, lid = world()
    assert c.post("/api/admin/claim", json={"kind": "listing", "id": str(lid)}, headers=ADM).json()["claimed"] is True
    with SessionLocal() as db:
        from app.models import Admin, AdminSession
        from app.admin_auth import _hash
        db.add(Admin(tg_id="777", role="moderator", active=True, created_at=now()))
        db.add(AdminSession(token_hash=_hash("mod777"), tg_id="777", expires_at=now() + dt.timedelta(hours=1)))
        db.commit()
    other = {"X-Admin-Session": "mod777"}
    busy = c.post("/api/admin/claim", json={"kind": "listing", "id": str(lid)}, headers=other)
    assert busy.status_code == 409 and "999" in busy.json()["detail"]
    assert c.get("/api/admin/claims?kind=listing", headers=other).json() == {str(lid): "999"}
    c.post("/api/admin/claim", json={"kind": "listing", "id": str(lid), "release": True}, headers=ADM)
    assert c.post("/api/admin/claim", json={"kind": "listing", "id": str(lid)}, headers=other).json()["claimed"] is True
    # undo a decision
    assert c.post(f"/api/admin/listings/{lid}/reject?reason=нет+фото", headers=ADM).status_code == 200
    assert c.post(f"/api/admin/listings/{lid}/undo", headers=ADM).json()["status"] == "pending"
    assert c.post(f"/api/admin/listings/{lid}/undo", headers=ADM).status_code == 409        # nothing left to undo


def test_temporary_block_ends_by_itself():
    c, lid = world()
    r = c.post("/api/admin/users/2/block-for", json={"reason": "спам", "days": 3}, headers=ADM)
    assert r.status_code == 200
    with SessionLocal() as db:
        assert db.get(User, "2").blocked is True
        db.get(User, "2").blocked_until = now() - dt.timedelta(minutes=1); db.commit()
        assert adminplus.unblock_expired(db) == 1
        assert db.get(User, "2").blocked is False and db.get(User, "2").blocked_until is None


def test_audit_filters_and_reports_and_staff_detail():
    c, lid = world()
    c.post(f"/api/admin/listings/{lid}/reject?reason=нет+фото", headers=ADM)
    rows = c.get("/api/admin/audit?action=listing.reject&object_id=" + str(lid), headers=ADM).json()
    assert len(rows) == 1 and rows[0]["actor"] == "999"
    assert c.get("/api/admin/audit?q=нет+фото", headers=ADM).json()
    assert c.get("/api/admin/audit?action=nothing", headers=ADM).json() == []
    assert c.get("/api/admin/audit?since=bad", headers=ADM).status_code == 400
    c.post("/api/reports", json={"target_type": "listing", "target_id": str(lid), "reason": "spam"}, headers=as_user(2))
    summ = c.get("/api/admin/reports-summary", headers=ADM).json()
    assert summ["by_reason"][0]["reason"] == "spam" and summ["top"][0]["id"] == str(lid)
    det = c.get("/api/admin/staff-detail", headers=ADM).json()
    assert det[0]["actor"] == "999" and det[0]["rejected"] == 1 and det[0]["reject_pct"] == 100


def test_scheduled_broadcast_groups_system_and_sessions():
    c, lid = world()
    with SessionLocal() as db:
        db.get(User, "1").employer_status = "approved"; db.commit()
    b = c.post("/api/admin/broadcasts", json={"text": "Привет, работодатели!", "audience": "employers"}, headers=ADM).json()
    assert b["total"] == 1 and b["status"] == "draft"
    soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
    s = c.post("/api/admin/broadcasts", json={"text": "Отложенная рассылка", "audience": "all", "send_at": soon}, headers=ADM).json()
    assert s["status"] == "scheduled"
    assert c.post("/api/admin/broadcasts", json={"text": "Прошлое время", "send_at": "2020-01-01T00:00:00+00:00"}, headers=ADM).status_code == 400
    assert c.post("/api/admin/broadcasts", json={"text": "Плохая группа", "audience": "xx"}, headers=ADM).status_code == 400
    with SessionLocal() as db:
        from app.models import Broadcast
        db.get(Broadcast, s["id"]).send_at = now() - dt.timedelta(minutes=1); db.commit()
        sent = []
        adminx.broadcast_tick(db, lambda tg, text: sent.append(tg) or True)
        assert db.get(Broadcast, s["id"]).status in {"sending", "done"}
    sysx = c.get("/api/admin/system", headers=ADM).json()
    assert sysx["db"] is True and "disk_free_mb" in sysx
    assert c.get("/api/admin/my-sessions", headers=ADM).json()
    assert c.post("/api/admin/staff/999/force-logout", headers=ADM).json()["closed"] >= 1
    assert c.get("/api/admin/counters", headers=ADM).status_code == 401          # the session was closed


def test_resume_and_ticket_bulk():
    c, lid = world()
    with SessionLocal() as db:
        for tg in ("1", "2"):
            db.add(Resume(owner_tg_id=tg, position="Повар", city="Сеул", status="pending")); db.commit()
    assert c.post("/api/admin/resumes-bulk/approve", json={"ids": ["1", "2"]}, headers=ADM).json() == {"done": 2}
    t1 = c.post("/api/tickets", json={"text": "Первое обращение"}, headers=as_user(1)).json()["id"]
    t2 = c.post("/api/tickets", json={"text": "Второе обращение"}, headers=as_user(2)).json()["id"]
    assert c.post("/api/admin/tickets-bulk-close", json={"ids": [str(t1), str(t2)]}, headers=ADM).json() == {"done": 2}
