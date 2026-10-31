import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import botbrowse as bb
from app import botadmin as ba
from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import DatingLike, DatingProfile, Listing, Report, User
from app.payments import now
from tests.tg import accept_terms, as_user, reset_db

ADMIN = {"X-Admin-Session": "test-session-token"}
THIS_YEAR = now().year


@pytest.fixture()
def client(tmp_path, monkeypatch):
    assert engine.url.drivername == "sqlite"
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    reset_db()
    bb.STATE.clear()
    bb.WAIT_QUERY.clear()
    with TestClient(app) as c:
        for uid in (1, 2, 3, 4):
            accept_terms(c, uid)
        yield c


def profile(uid, age=25, gender="f", region="seoul", name="Аня", city="Сеул", status="published"):
    with SessionLocal() as db:
        db.add(DatingProfile(owner_tg_id=str(uid), birth_year=THIS_YEAR - age, gender=gender, city=city, region_code=region,
                             first_name=name, bio=f"О себе {uid}", status=status, photo_key=f"k{uid}.jpg"))
        db.commit()
        return db.scalar(select(DatingProfile.id).where(DatingProfile.owner_tg_id == str(uid)))


def feed(client, user, **params):
    return client.get("/api/dating", params=params, headers=as_user(user)).json()


def test_name_is_saved_checked_and_shown(client):
    body = {"birth_year": THIS_YEAR - 28, "gender": "f", "region_code": "seoul", "bio": "", "first_name": "  Аня  "}
    r = client.put("/api/dating/profile", json=body, headers=as_user(2))
    assert r.status_code == 200 and r.json()["name"] == "Аня"
    for bad in ("Аня @anya", "https://x.ru", "+82 10 1234 5678"):
        assert client.put("/api/dating/profile", json={**body, "first_name": bad}, headers=as_user(2)).status_code == 400
    assert client.get("/api/dating/me", headers=as_user(2)).json()["name"] == "Аня"
    row = client.get("/api/admin/dating", headers=ADMIN).json()[0]
    assert row["name"] == "Аня"                                  # the moderator sees the name next to the photo
    assert "Аня" in ba.dating_queue(SessionLocal()).text


def test_filters_age_and_region(client):
    profile(2, age=20, region="seoul", name="А")
    profile(3, age=30, region="busan", name="Б")
    profile(4, age=40, region="seoul", name="В")
    assert {p["name"] for p in feed(client, 1)} == {"А", "Б", "В"}
    assert {p["name"] for p in feed(client, 1, age_min=18, age_max=25)} == {"А"}
    assert {p["name"] for p in feed(client, 1, age_min=26, age_max=35)} == {"Б"}
    assert {p["name"] for p in feed(client, 1, age_min=36)} == {"В"}
    assert {p["name"] for p in feed(client, 1, region="seoul")} == {"А", "В"}
    assert {p["name"] for p in feed(client, 1, region="seoul", age_min=30)} == {"В"}
    assert feed(client, 1, region="jeju") == []


def test_likes_me_screen(client):
    p2, p3 = profile(2, name="А"), profile(3, name="Б")
    profile(1, age=27, gender="m", name="Я")
    client.post(f"/api/dating/{profile_id_of(1)}/like", headers=as_user(2))
    client.post(f"/api/dating/{profile_id_of(1)}/like", headers=as_user(3))
    r = client.get("/api/dating/likes-me", headers=as_user(1)).json()
    assert r["count"] == 2 and {p["name"] for p in r["items"]} == {"А", "Б"}
    assert "owner" not in str(r) and "tg" not in str(r).lower()
    # like back from that screen -> mutual, chat appears, the person leaves the "liked me" list
    back = client.post(f"/api/dating/{p2}/like", headers=as_user(1)).json()
    assert back["match"] is True
    assert client.get("/api/dating/likes-me", headers=as_user(1)).json()["count"] == 1
    # a blocked person is not shown
    client.post("/api/users/3/block", headers=as_user(1))
    assert client.get("/api/dating/likes-me", headers=as_user(1)).json()["count"] == 0


def profile_id_of(uid):
    with SessionLocal() as db:
        return db.scalar(select(DatingProfile.id).where(DatingProfile.owner_tg_id == str(uid)))


def test_unpublished_likers_stay_hidden(client):
    profile(2, status="pending")
    profile(1, gender="m")
    client.post(f"/api/dating/{profile_id_of(1)}/like", headers=as_user(2))
    assert client.get("/api/dating/likes-me", headers=as_user(1)).json()["count"] == 0


# ---- bot ----

@pytest.fixture()
def db(client):
    with SessionLocal() as session:
        yield session


def test_bot_dating_filters_report_and_likes(client, db):
    profile(2, age=20, name="А"); profile(3, age=30, name="Б")
    view = bb.act(db, 1, "b:dt")
    assert "А" in view.text or "Б" in view.text
    for _ in range(4):
        view = bb.act(db, 1, "b:da")           # cycles age presets: any -> 18-25 -> 26-35 -> 36+
    assert "Любой" in str(view.rows)
    assert "А," in bb.act(db, 1, "b:da").text   # 18-25
    assert "Б," in bb.act(db, 1, "b:da").text   # 26-35
    assert "Регион" in str(bb.act(db, 1, "b:dr").rows) or len(bb.act(db, 1, "b:dr").rows) > 5
    pid = profile_id_of(3)
    assert "На что жалоба" in bb.act(db, 1, f"b:dx:{pid}").text
    done = bb.act(db, 1, f"b:dy:{pid}:fake")
    assert done.alert.startswith("Жалоба отправлена")
    report = db.scalar(select(Report))
    assert report.target_type == "dating" and report.target_id == str(pid) and report.reporter_tg_id == "1" and report.reason == "fake"
    bb.act(db, 1, f"b:dy:{pid}:fake")            # a second report from the same person is not duplicated
    assert len(db.scalars(select(Report)).all()) == 1
    assert bb.act(db, 1, f"b:dy:{pid}:nonsense").alert == ""   # an unknown reason creates nothing
    assert len(db.scalars(select(Report)).all()) == 1


def test_bot_likes_me_mode(client, db):
    profile(2, name="А"); profile(1, gender="m", name="Я")
    client.post(f"/api/dating/{profile_id_of(1)}/like", headers=as_user(2))
    view = bb.act(db, 1, "b:dt")
    assert "Меня лайкнули (1)" in str(view.rows)
    liked = bb.act(db, 1, "b:dm")
    assert "Вас лайкнули" in liked.text and "А," in liked.text


def test_bot_find_across_sections_and_deeplink(client, db):
    with SessionLocal() as s:
        s.add(Listing(category="job", title="Повар в Сеуле", city="Сеул", author_telegram_id="2", status="approved", contact="@hr_x1",
                      description="d", published_at=now()))
        s.add(Listing(category="marketplace", title="Диван повар", city="Пусан", author_telegram_id="2", status="approved", contact="",
                      description="d", published_at=now()))
        s.commit()
    st = bb.STATE.setdefault(1, {"sec": "f", "idx": 0, "q": "повар", "region": ""})
    st["ids"] = bb._search(db, st)
    view = bb.listing_view(db, 1, st)
    assert "Поиск по всем" in view.text and len(st["ids"]) == 2     # both sections found
    lid = st["ids"][0]
    card = bb.single_view(db, lid)
    assert "повар" in card.text.lower() and any(b.get("app") == f"?listing={lid}" for r in card.rows for b in r)
    assert "не найдено" in bb.single_view(db, 9999).text


def test_config_endpoint_never_crashes_without_telegram(client, monkeypatch):
    from app import main
    from app import webauth
    main._config_cache.clear()
    monkeypatch.setattr(webauth, "bot_username", lambda: (_ for _ in ()).throw(RuntimeError("no network")))
    assert client.get("/api/config").json() == {"bot": ""}
    monkeypatch.setattr(webauth, "bot_username", lambda: "korea_hub_bot")
    main._config_cache.clear()
    assert client.get("/api/config").json() == {"bot": "korea_hub_bot"}
