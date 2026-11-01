import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import DatingProfile
from tests.tg import as_user, reset_db, accept_terms
from tests.test_dating import ADMIN, publish


@pytest.fixture()
def client(tmp_path, monkeypatch):
    assert engine.url.drivername == "sqlite"
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def new_ad(client, **extra):
    body = {"title": "Скидка в кафе", "text": "Кофе -20%", "link": "https://example.com/x"} | extra
    r = client.post("/api/admin/ads", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_ads_never_in_first_three_and_marked(client):
    new_ad(client)
    assert client.get("/api/ads?organic=2", headers=as_user(1)).json() == []        # too short a list
    slots = client.get("/api/ads?organic=10", headers=as_user(1)).json()
    assert slots and slots[0]["at"] >= 3 and slots[0]["ad"]["label"] == "Реклама"
    assert client.get("/api/ads?organic=10&page=1", headers=as_user(1)).json() == []  # first page only


def test_adult_ad_only_for_confirmed_age_and_off_switch(client):
    aid = new_ad(client, adult_only=True)
    assert client.get("/api/ads?organic=10", headers=as_user(1)).json() == []
    publish(client, 1)
    with SessionLocal() as db:
        db.query(DatingProfile).one().selfie_status = "approved"
        db.commit()
    assert len(client.get("/api/ads?organic=10", headers=as_user(1)).json()) == 1
    assert client.get("/api/ads?organic=10").json() == []                              # a guest does not see it
    client.put(f"/api/admin/ads/{aid}/toggle", headers=ADMIN)
    assert client.get("/api/ads?organic=10", headers=as_user(1)).json() == []


def test_ad_stats_and_bad_input(client):
    aid = new_ad(client)
    client.get("/api/ads?organic=10", headers=as_user(1))
    assert client.post(f"/api/ads/{aid}/click").status_code == 204
    row = client.get("/api/admin/ads", headers=ADMIN).json()[0]
    assert row["shows"] == 1 and row["clicks"] == 1 and row["ctr"] == 100.0
    assert client.post("/api/admin/ads", json={"title": "Без ссылки"}, headers=ADMIN).status_code == 400
    assert client.post("/api/admin/ads", json={"title": "Нет", "listing_id": 9999}, headers=ADMIN).status_code == 404


def test_promo_claim_once_marked_and_stop_keeps_given(client):
    publish(client, 1)
    cid = client.post("/api/admin/campaigns", json={"title": "Неделя VIP", "service_code": "dating_vip", "days": 3,
                                                   "audience": "dating"}, headers=ADMIN).json()["id"]
    assert client.get("/api/promos", headers=as_user(2)).json() == []                  # not in the audience
    offer = client.get("/api/promos", headers=as_user(1)).json()
    assert offer and offer[0]["can_claim"] is True
    assert client.post(f"/api/promos/{cid}/claim", headers=as_user(1)).status_code == 201
    assert client.post(f"/api/promos/{cid}/claim", headers=as_user(1)).status_code == 409
    with SessionLocal() as db:
        p = db.query(DatingProfile).one()
        assert p.vip_promo is True and p.vip_until is not None
    client.put(f"/api/admin/campaigns/{cid}/stop", headers=ADMIN)
    with SessionLocal() as db:
        assert db.query(DatingProfile).one().vip_until is not None                      # an early stop takes nothing back
    assert client.post(f"/api/promos/{cid}/claim", headers=as_user(1)).status_code == 409


def test_auto_campaign_is_given_once_at_start(client):
    publish(client, 1)
    client.post("/api/admin/campaigns", json={"title": "Всем VIP", "service_code": "dating_vip", "days": 2,
                                              "audience": "dating", "mode": "auto"}, headers=ADMIN)
    from app import ads
    with SessionLocal() as db:
        ads.housekeeping(db)
        ads.housekeeping(db)
        assert db.query(DatingProfile).one().vip_promo is True
        from app.ads import PromoClaim
        assert db.query(PromoClaim).count() == 1


def test_promo_for_a_listing_needs_the_listing_and_marks_it_free(client):
    from tests.test_catalog import make
    from app.models import Placement
    lid = make(client, user=1, category="job", title="Повар", city="Сеул")
    cid = client.post("/api/admin/campaigns", json={"title": "Бесплатное поднятие", "service_code": "job_bump", "days": 1,
                                                   "audience": "sellers"}, headers=ADMIN).json()["id"]
    offer = client.get("/api/promos", headers=as_user(1)).json()
    assert offer[0]["needs_listing"] is True and offer[0]["can_claim"] is True
    assert client.post(f"/api/promos/{cid}/claim", json={}, headers=as_user(1)).status_code == 400          # no listing chosen
    assert client.post(f"/api/promos/{cid}/claim", json={"listing_id": lid}, headers=as_user(2)).status_code in {400, 409}   # not yours
    assert client.post(f"/api/promos/{cid}/claim", json={"listing_id": lid}, headers=as_user(1)).status_code == 201
    with SessionLocal() as db:
        pl = db.query(Placement).one()
        assert pl.promo is True and pl.kind == "bump"
    # the unattended mode is only for dating profiles
    aid = client.post("/api/admin/campaigns", json={"title": "Всем", "service_code": "job_bump", "mode": "auto"}, headers=ADMIN).json()["id"]
    from app import ads
    with SessionLocal() as db:
        ads.housekeeping(db)
        assert db.query(Placement).count() == 1


def test_saved_search_does_not_alert_about_a_hidden_listing(client):
    from tests.test_catalog import make
    from app import searches
    from app.models import Listing
    client.post("/api/me/searches", json={"q": "повар"}, headers=as_user(2))
    lid = make(client, user=1, category="job", title="Повар в кафе", city="Сеул")
    with SessionLocal() as db:
        db.get(Listing, lid).status = "hidden"
        db.commit()
        from app.models import SavedSearch
        for row in db.query(SavedSearch):
            row.last_checked_at = payments_now_minus()
        db.commit()
        assert searches.new_matches(db) == []


def payments_now_minus():
    import datetime as dt
    from app.payments import now
    return now() - dt.timedelta(days=1)
