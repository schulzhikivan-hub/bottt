import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import DatingProfile, Listing, Notification
from tests.tg import as_user, reset_db, accept_terms
from tests.test_catalog import make
from tests.test_dating import publish

ADMIN = {"X-Admin-Session": "test-session-token"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def test_moderator_edits_every_field_with_a_journal_and_a_notice(client):
    lid = make(client, user=1, title="Cook wanted", category="job", price="")
    r = client.put(f"/api/admin/listings/{lid}/full", json={"title": "Cook for a cafe", "price_amount": 2500000, "employment": "full", "visas": ["E-9", "F-4"],
                                                            "housing_provided": True, "description": "Полный день"}, headers=ADMIN)
    assert r.status_code == 200 and set(r.json()["changed"]) >= {"title", "price_amount", "employment", "visas", "housing_provided"}
    with SessionLocal() as db:
        item = db.get(Listing, lid)
        assert item.title == "Cook for a cafe" and item.price_amount == 2500000 and "cafe" in item.search_text and item.visas
        assert db.query(Notification).filter(Notification.user_tg_id == "1", Notification.text.like("%поправил ваше объявление%")).count() == 1
    assert client.put(f"/api/admin/listings/{lid}/full", json={"title": "Cook for a cafe"}, headers=ADMIN).json() == {"changed": []}        # nothing new
    assert client.put(f"/api/admin/listings/{lid}/full", json={"rooms": 3}, headers=ADMIN).json() == {"changed": []}                          # not a job field: dropped
    assert client.get("/api/catalog", params={"q": "cafe"}).json()["items"][0]["id"] == lid                                                    # the search sees it at once
    h = client.get(f"/api/admin/listings/{lid}/history", headers=ADMIN).json()
    assert any(j["action"] == "listing.admin_edit" for j in h["journal"]) and h["flags"]["quarantine"] is False
    assert client.put(f"/api/admin/listings/999/full", json={"title": "Нет такого"}, headers=ADMIN).status_code == 404
    assert client.put(f"/api/admin/listings/{lid}/full", json={"title": "x"}, headers=ADMIN).status_code == 422


def test_dating_list_filters_and_edit_and_delete(client):
    pid = publish(client, 1)
    other = publish(client, 2)
    r = client.get("/api/admin/dating-list?status=published", headers=ADMIN).json()
    assert r["total"] == 2 and {i["id"] for i in r["items"]} == {pid, other}
    assert client.get("/api/admin/dating-list?flag=suspect", headers=ADMIN).json()["total"] == 0
    e = client.put(f"/api/admin/dating/{pid}/edit", json={"bio": "Новый текст о себе", "paused": True, "reason": "правка по жалобе"}, headers=ADMIN)
    assert set(e.json()["changed"]) == {"bio", "paused"}
    assert client.get("/api/admin/dating-list?flag=paused", headers=ADMIN).json()["total"] == 1
    assert client.delete(f"/api/admin/dating/{pid}/photo", headers=ADMIN).status_code == 200
    with SessionLocal() as db:
        assert db.get(DatingProfile, pid).status == "pending" and db.get(DatingProfile, pid).photo_key is None
    assert client.post(f"/api/admin/dating/{other}/delete", json={"reason": "x"}, headers=ADMIN).status_code == 422
    assert client.post(f"/api/admin/dating/{other}/delete", json={"reason": "фото не его"}, headers=ADMIN).json() == {"deleted": True}
    with SessionLocal() as db:
        assert db.get(DatingProfile, other) is None
        assert db.query(Notification).filter(Notification.user_tg_id == "2", Notification.text.like("%удалена модератором%")).count() == 1
