from fastapi.testclient import TestClient

from app import admin_ops as ops
from app.database import SessionLocal
from app.main import app
from app.models import Listing, Report, Review
from app.payments import now
from tests.tg import accept_terms, as_user, reset_db, TEST_ADMIN_SESSION
import datetime as dt

ADMIN = {"X-Admin-Session": TEST_ADMIN_SESSION}


def setup_chat(talk=True):
    reset_db()
    c = TestClient(app)
    for u in (1, 2):
        accept_terms(c, u)
    with SessionLocal() as db:
        item = Listing(author_telegram_id="1", category="market", title="Диван", description="x" * 40, city="Сеул",
                       status="approved", contact="c", expires_at=now() + dt.timedelta(days=5))
        db.add(item); db.commit(); lid = item.id
    conv = c.post(f"/api/listings/{lid}/conversation", headers=as_user(2)).json()["id"]
    c.post(f"/api/conversations/{conv}/messages", json={"text": "Здравствуйте"}, headers=as_user(2))
    if talk:
        c.post(f"/api/conversations/{conv}/messages", json={"text": "Привет"}, headers=as_user(1))
    return c, lid


def test_review_needs_two_sided_chat():
    c, lid = setup_chat(talk=False)
    r = c.post("/api/reviews", json={"listing_id": lid, "rating": 5}, headers=as_user(2))
    assert r.status_code == 403
    assert c.get(f"/api/listings/{lid}/review-state", headers=as_user(2)).json()["can"] is False


def test_review_flow_rating_and_rules():
    c, lid = setup_chat()
    assert c.get(f"/api/listings/{lid}/review-state", headers=as_user(2)).json()["can"] is True
    assert c.post("/api/reviews", json={"listing_id": lid, "rating": 5}, headers=as_user(1)).status_code == 400  # about myself
    assert c.post("/api/reviews", json={"listing_id": lid, "rating": 9}, headers=as_user(2)).status_code == 422
    assert c.post("/api/reviews", json={"listing_id": lid, "rating": 4, "text": "пишите +82 10 1234 5678"}, headers=as_user(2)).status_code == 400
    ok = c.post("/api/reviews", json={"listing_id": lid, "rating": 4, "text": "Всё хорошо"}, headers=as_user(2))
    assert ok.status_code == 201 and ok.json()["rating"] == 4.0
    assert c.post("/api/reviews", json={"listing_id": lid, "rating": 5}, headers=as_user(2)).status_code == 409
    pub = c.get(f"/api/authors/{lid}/reviews").json()
    assert pub["reviews"] == 1 and pub["items"][0]["text"] == "Всё хорошо" and "reviewer" not in pub["items"][0]
    assert c.get(f"/api/listings/{lid}").json()["author"]["rating"] == 4.0


def test_hidden_review_leaves_rating_and_report_hides_it():
    c, lid = setup_chat()
    rid = c.post("/api/reviews", json={"listing_id": lid, "rating": 1, "text": "плохо"}, headers=as_user(2)).json()["id"]
    assert c.post("/api/reports", json={"target_type": "review", "target_id": str(rid), "reason": "spam"}, headers=as_user(1)).status_code == 201
    with SessionLocal() as db:
        rep = db.query(Report).filter_by(target_type="review").one()
        ops.decide_report(db, "999", rep.id, "resolve")
        assert db.get(Review, rid).status == "hidden"
    assert c.get(f"/api/authors/{lid}/reviews").json()["reviews"] == 0
    assert c.post(f"/api/admin/reviews/{rid}/show", headers=ADMIN).json()["status"] == "published"
    assert c.get("/api/admin/reviews", headers=ADMIN).json()[0]["id"] == rid
    assert c.delete(f"/api/reviews/{rid}", headers=as_user(1)).status_code == 404   # only the writer deletes
    assert c.delete(f"/api/reviews/{rid}", headers=as_user(2)).status_code == 200
