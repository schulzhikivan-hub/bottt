import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import DatingLike, DatingPrivatePhoto, DatingProfile, Notification
from app.payments import now
from tests.tg import accept_terms, as_user, reset_db

ADMIN = {"X-Admin-Session": "test-session-token"}
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200


@pytest.fixture()
def client(tmp_path, monkeypatch):
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3, 4):
            accept_terms(c, uid)
        yield c


def make(client, user, gender="f", **extra):
    body = {"birth_year": 1990, "gender": gender, "city": "Сеул", "bio": "Люблю кофе", **extra}
    pid = client.put("/api/dating/profile", json=body, headers=as_user(user)).json()["id"]
    client.post("/api/dating/photo", files={"file": ("me.jpg", JPEG, "image/jpeg")}, headers=as_user(user))
    assert client.post(f"/api/admin/dating/{pid}/approve", headers=ADMIN).status_code == 200
    return pid


def feed(client, user, query=""):
    return [p["id"] for p in client.get("/api/dating?" + query, headers=as_user(user)).json()]


def update(pid, **fields):
    with SessionLocal() as db:
        row = db.get(DatingProfile, pid)
        for k, v in fields.items():
            setattr(row, k, v)
        db.commit()


def test_age_comes_from_the_date_of_birth_and_a_minor_is_not_created(client):
    today = dt.date.today()
    minor = (today - dt.timedelta(days=17 * 366)).isoformat()
    r = client.put("/api/dating/profile", json={"birth_date": minor, "gender": "f", "city": "Сеул"}, headers=as_user(1))
    assert r.status_code == 400 and "18" in r.json()["detail"]
    assert client.get("/api/dating/me", headers=as_user(1)).json() is None        # nothing was saved
    adult = (today - dt.timedelta(days=30 * 366)).isoformat()
    ok = client.put("/api/dating/profile", json={"birth_date": adult, "gender": "f", "city": "Сеул"}, headers=as_user(1))
    assert ok.status_code == 200
    me = client.get("/api/dating/me", headers=as_user(1)).json()
    assert me["age"] == 30 and me["age_confirmed"] is False and me["birth_date"] == adult
    assert client.put("/api/dating/profile", json={"gender": "f", "city": "Сеул"}, headers=as_user(1)).status_code == 400   # no date at all


def test_sympathy_life_cycle_tabs_cancel_and_mutual(client):
    p1, p2, p3 = make(client, 1), make(client, 2, "m"), make(client, 3)
    first = client.post(f"/api/dating/{p2}/like", headers=as_user(1)).json()
    assert first["mutual"] is False and first["can_cancel_until"]
    assert p1 in feed(client, 2, "mode=to_me") and client.get("/api/dating/counts", headers=as_user(2)).json()["to_me"] == 1
    assert feed(client, 1, "mode=from_me") == [p2] and p2 not in feed(client, 1)         # the liked one leaves the feed
    assert any("Вам симпатизируют" in n["text"] for n in client.get("/api/me/notifications", headers=as_user(2)).json()["items"])
    # take it back: the notice disappears from the other person's center
    assert client.delete(f"/api/dating/{p2}/like", headers=as_user(1)).json() == {"cancelled": True}
    assert not any("Вам симпатизируют" in n["text"] for n in client.get("/api/me/notifications", headers=as_user(2)).json()["items"])
    assert client.delete(f"/api/dating/{p2}/like", headers=as_user(1)).status_code == 404   # nothing to take back
    # the 5 minutes pass
    client.post(f"/api/dating/{p2}/like", headers=as_user(1))
    with SessionLocal() as db:
        row = db.query(DatingLike).filter_by(from_tg="1", to_tg="2").one()
        row.created_at = now() - dt.timedelta(minutes=6)
        db.commit()
    assert client.delete(f"/api/dating/{p2}/like", headers=as_user(1)).status_code == 409
    # the answer makes it mutual and opens the chat
    back = client.post(f"/api/dating/{p1}/like", headers=as_user(2)).json()
    assert back["mutual"] is True and back["conversation_id"]
    assert client.delete(f"/api/dating/{p2}/like", headers=as_user(1)).status_code == 409   # mutual: not cancellable
    mut1 = client.get("/api/dating?mode=mutual", headers=as_user(1)).json()
    assert [x["id"] for x in mut1] == [p2] and mut1[0]["conversation_id"] == back["conversation_id"]
    assert [x["id"] for x in client.get("/api/dating?mode=mutual", headers=as_user(2)).json()] == [p1]
    assert feed(client, 1, "mode=to_me") == [] and feed(client, 1, "mode=from_me") == []     # it moved to «Взаимные»
    assert any("Взаимная симпатия" in n["text"] for n in client.get("/api/me/notifications", headers=as_user(1)).json()["items"])
    assert client.get("/api/dating?mode=bad", headers=as_user(1)).status_code == 400


def test_pause_makes_a_profile_unreachable_and_unable_to_like(client):
    p1, p2 = make(client, 1), make(client, 2, "m")
    assert client.get(f"/api/dating/{p2}/photo").status_code == 200
    client.post("/api/dating/pause", json={"on": True}, headers=as_user(2))
    assert p2 not in feed(client, 1)
    assert client.post(f"/api/dating/{p2}/like", headers=as_user(1)).status_code == 404
    assert client.get(f"/api/dating/{p2}", headers=as_user(1)).status_code == 404          # not even by the link
    assert client.get(f"/api/dating/{p2}/photo").status_code == 404
    assert client.get(f"/api/dating/{p2}", headers=as_user(2)).status_code == 200            # the owner still sees it
    assert client.post(f"/api/dating/{p1}/like", headers=as_user(2)).status_code == 403     # a paused profile can not like
    client.post("/api/dating/pause", json={"on": False}, headers=as_user(2))
    assert p2 in feed(client, 1)


def test_sympathies_collect_during_a_pause_and_wait(client):
    p1, p2 = make(client, 1), make(client, 2, "m")
    client.post(f"/api/dating/{p1}/like", headers=as_user(2))          # 2 likes 1 first
    client.post("/api/dating/pause", json={"on": True}, headers=as_user(1))
    assert client.get("/api/dating/counts", headers=as_user(1)).json()["to_me"] == 0            # not shown during the pause
    client.post("/api/dating/pause", json={"on": False}, headers=as_user(1))
    assert feed(client, 1, "mode=to_me") == [p2]                                               # but nothing was lost


def test_invisible_profile_is_in_no_list_but_opens_by_link_and_can_like(client):
    p1, p2 = make(client, 1), make(client, 2, "m")
    assert client.post("/api/dating/invisible", json={"on": True}, headers=as_user(2)).status_code == 409   # not paid
    update(p2, invisible_until=now() + dt.timedelta(days=5), invisible_on=True)
    assert p2 not in feed(client, 1)
    assert client.get(f"/api/dating/{p2}", headers=as_user(1)).status_code == 200            # by the link
    assert client.post(f"/api/dating/{p1}/like", headers=as_user(2)).json()["mutual"] is False   # it may like
    assert feed(client, 1, "mode=to_me") == []                                               # but is not in «Мне симпатизируют»
    back = client.post(f"/api/dating/{p2}/like", headers=as_user(1)).json()
    assert back["mutual"] is True                                                            # the answer still works
    assert feed(client, 1, "mode=mutual") == []                                              # and it is not in «Взаимные»
    assert client.get("/api/dating/me", headers=as_user(2)).json()["invisible"] is True
    client.post("/api/dating/invisible", json={"on": False}, headers=as_user(2))
    assert p2 in feed(client, 3)


def test_blocked_deleted_and_removed_profiles_take_their_sympathies_with_them(client):
    p1, p2, p3 = make(client, 1), make(client, 2, "m"), make(client, 3)
    client.post(f"/api/dating/{p2}/like", headers=as_user(1))
    client.post(f"/api/dating/{p3}/like", headers=as_user(1))
    assert client.post("/api/admin/users/2/block", json={"reason": "спам"}, headers={"X-Admin-Session": "test-session-token"}).status_code == 200
    assert feed(client, 1, "mode=from_me") == [p3]                                             # a blocked person is hidden
    assert client.delete("/api/dating/profile", headers=as_user(3)).json() == {"deleted": True}
    assert feed(client, 1, "mode=from_me") == []
    with SessionLocal() as db:
        assert db.query(DatingLike).filter_by(to_tg="3").count() == 0                          # the sympathies were removed
        assert db.query(DatingProfile).filter_by(owner_tg_id="3").count() == 0


def test_filters_and_lift_of_vip_and_promo(client):
    a, b, c = make(client, 1), make(client, 2, "m"), make(client, 3)
    update(a, seeking="m", goal="marriage", height=170, visa="F-6", birth_year=1985)
    update(b, seeking="f", goal="chat", height=185, visa="E-7")
    update(c, seeking="any", goal="marriage", height=160, visa="F-6")
    assert set(feed(client, 4, "seeking=m")) == {a, c}                          # «any» fits everyone
    assert set(feed(client, 4, "goal=marriage")) == {a, c} and feed(client, 4, "visa=E-7") == [b]
    assert feed(client, 4, "height_min=180") == [b] and set(feed(client, 4, "height_max=170")) == {a, c}
    assert feed(client, 4, "age_min=18&age_max=25") == []                       # all three are older
    with SessionLocal() as db:
        db.add(DatingPrivatePhoto(profile_id=c, key="x.jpg", status="live"))
        db.add(DatingPrivatePhoto(profile_id=a, key="y.jpg", status="pending"))   # waiting for a moderator: does not count
        db.commit()
    assert feed(client, 4, "private=true") == [c]
    item = [p for p in client.get("/api/dating?private=true", headers=as_user(4)).json()][0]
    assert item["private_photos"] == 1 and "key" not in str(item)                # only the number, never a file
    assert feed(client, 4, "verified=true") == []
    update(a, selfie_status="approved")
    assert feed(client, 4, "verified=true") == [a]
    # VIP is lifted, free promo VIP only has the mark; the filter «только VIP» shows both
    update(a, vip_until=now() + dt.timedelta(days=3), vip_promo=False)
    update(b, vip_until=now() + dt.timedelta(days=3), vip_promo=True)
    order = feed(client, 4)
    assert order[0] == a and order.index(c) < order.index(b)                      # paid first; the free one is not lifted
    marks = {p["id"]: p for p in client.get("/api/dating", headers=as_user(4)).json()}
    assert marks[a]["vip"] and not marks[a]["vip_free"] and marks[b]["vip"] and marks[b]["vip_free"]
    assert set(feed(client, 4, "vip=true")) == {a, b}


def test_super_sympathy_comes_first_and_returns_when_cancelled(client):
    p1, p2, p3 = make(client, 1), make(client, 2, "m"), make(client, 3)
    assert client.post(f"/api/dating/{p3}/like", json={"super": True}, headers=as_user(1)).status_code == 409     # no credits
    update(p1, super_credits=1)
    assert client.post(f"/api/dating/{p3}/like", json={"super": True}, headers=as_user(1)).json()["mutual"] is False
    assert client.get("/api/dating/me", headers=as_user(1)).json()["super_credits"] == 0
    client.post(f"/api/dating/{p3}/like", headers=as_user(2))                       # an ordinary one, later
    got = client.get("/api/dating?mode=to_me", headers=as_user(3)).json()
    assert [x["id"] for x in got] == [p1, p2] and got[0]["super"] is True and got[1]["super"] is False
    assert client.delete(f"/api/dating/{p3}/like", headers=as_user(1)).status_code == 200
    assert client.get("/api/dating/me", headers=as_user(1)).json()["super_credits"] == 1       # the unused credit came back


def test_minor_report_hides_the_profile_until_a_moderator_clears_it(client):
    p1 = make(client, 1)
    assert p1 in feed(client, 2)
    assert client.post("/api/reports", json={"target_type": "dating", "target_id": str(p1), "reason": "minor"}, headers=as_user(2)).status_code == 201
    assert p1 not in feed(client, 3) and client.get(f"/api/dating/{p1}", headers=as_user(3)).status_code == 404
    assert client.post(f"/api/admin/dating/{p1}/approve", headers=ADMIN).status_code == 200
    assert p1 in feed(client, 3)


def test_detail_records_views_and_admin_gets_conversion_stats(client):
    p1, p2 = make(client, 1), make(client, 2, "m")
    client.get(f"/api/dating/{p1}", headers=as_user(2))
    client.get(f"/api/dating/{p1}", headers=as_user(2))                              # the same day: counted once
    from app.models import DatingView
    with SessionLocal() as db:
        assert db.query(DatingView).filter_by(profile_id=p1).count() == 1
    client.post(f"/api/dating/{p2}/like", headers=as_user(1))
    client.post(f"/api/dating/{p1}/like", headers=as_user(2))
    stats = client.get("/api/admin/dating-stats", headers=ADMIN).json()
    assert stats["sympathies"] == 2 and stats["mutual_pairs"] == 1 and stats["talking_pairs"] == 0
