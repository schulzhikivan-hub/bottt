import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import engine
from app.main import app
from tests.tg import as_user, reset_db, accept_terms

ADMIN = {"X-Admin-Session": "test-session-token"}
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200
ADULT = 1990
MINOR = 2012


@pytest.fixture()
def client(tmp_path, monkeypatch):
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def profile(client, user, birth=ADULT, gender="f", city="Сеул"):
    return client.put("/api/dating/profile", json={"birth_year": birth, "gender": gender,
                                                  "city": city, "bio": "Люблю кофе"}, headers=as_user(user))


def photo(client, user, data=JPEG, name="me.jpg"):
    return client.post("/api/dating/photo", files={"file": (name, data, "image/jpeg")}, headers=as_user(user))


def publish(client, user):
    pid = profile(client, user).json()["id"]
    assert photo(client, user).status_code == 200
    assert client.post(f"/api/admin/dating/{pid}/approve", headers=ADMIN).status_code == 200
    return pid


def test_under_18_cannot_register(client):
    r = profile(client, 1, birth=MINOR)
    assert r.status_code == 400
    assert "18" in r.json()["detail"]


def test_gender_must_be_f_or_m(client):
    assert profile(client, 1, gender="x").status_code == 400


def test_photo_type_checked_by_content_not_name(client):
    profile(client, 1)
    r = photo(client, 1, data=b"<html>not an image</html>", name="photo.jpg")
    assert r.status_code == 400


def test_photo_needs_profile_first(client):
    assert photo(client, 1).status_code == 400


def test_publish_requires_photo(client):
    pid = profile(client, 1).json()["id"]
    r = client.post(f"/api/admin/dating/{pid}/approve", headers=ADMIN)
    assert r.status_code == 400


def test_editing_returns_profile_to_moderation(client):
    publish(client, 1)
    profile(client, 1, city="Пусан")
    me = client.get("/api/dating/me", headers=as_user(1)).json()
    assert me["status"] == "pending"


def test_browse_shows_only_published_and_not_self(client):
    publish(client, 1)
    profile(client, 2, gender="m")  # not published
    assert [p["id"] for p in client.get("/api/dating", headers=as_user(1)).json()] == []
    publish_pid = publish(client, 3)
    seen = client.get("/api/dating", headers=as_user(1)).json()
    assert [p["id"] for p in seen] == [publish_pid]
    assert "owner" not in seen[0] and "tg" not in str(seen[0])  # no contacts or user IDs


def test_photo_served_only_when_published(client):
    pid = publish(client, 1)
    assert client.get(f"/api/dating/{pid}/photo").status_code == 200
    profile(client, 1)  # back to pending
    assert client.get(f"/api/dating/{pid}/photo").status_code == 404


def test_mutual_like_opens_chat_only_between_them(client):
    p1 = publish(client, 1)
    p2 = publish(client, 2)
    assert client.post(f"/api/dating/{p2}/like", headers=as_user(1)).json() == {"match": False}
    r = client.post(f"/api/dating/{p1}/like", headers=as_user(2)).json()
    assert r["match"] is True
    conv = r["conversation_id"]
    sent = client.post(f"/api/conversations/{conv}/messages", json={"text": "Привет"}, headers=as_user(1))
    assert sent.status_code == 201
    assert client.get(f"/api/conversations/{conv}/messages", headers=as_user(3)).status_code == 404
    assert client.get("/api/conversations", headers=as_user(2)).json()[0]["listing_title"] == "Знакомство"


def test_liked_profiles_disappear_from_feed(client):
    publish(client, 1)
    p3 = publish(client, 3)
    client.post(f"/api/dating/{p3}/like", headers=as_user(1))
    assert client.get("/api/dating", headers=as_user(1)).json() == []


def test_blocked_user_hidden_from_feed(client):
    publish(client, 1)
    publish(client, 3)
    client.post("/api/users/3/block", headers=as_user(1))
    assert client.get("/api/dating", headers=as_user(1)).json() == []


def test_admin_rejects_without_key(client):
    pid = profile(client, 1).json()["id"]
    assert client.post(f"/api/admin/dating/{pid}/approve").status_code == 401


def test_report_on_profile_rejects_it(client):
    pid = publish(client, 1)
    r = client.post("/api/reports", json={"target_type": "dating", "target_id": str(pid),
                                          "reason": "fake"}, headers=as_user(2))
    assert r.status_code == 201
    rid = client.get("/api/admin/reports", headers=ADMIN).json()[0]["id"]
    client.post(f"/api/admin/reports/{rid}/resolve", headers=ADMIN)
    assert client.get("/api/dating", headers=as_user(2)).json() == []


def test_cannot_report_own_profile(client):
    pid = publish(client, 1)
    r = client.post("/api/reports", json={"target_type": "dating", "target_id": str(pid),
                                          "reason": "fake"}, headers=as_user(1))
    assert r.status_code == 400
