import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal, engine
from app.main import app
from app.models import User
from tests.tg import as_user, reset_db, accept_terms
from tests.test_catalog import make
from tests.test_dating import ADMIN, publish


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def test_money_report_and_vip_history_count_paid_and_gifts(client):
    publish(client, 1)
    with SessionLocal() as db:
        db.get(User, "1").balance_stars = 300
        db.commit()
    client.post("/api/dating/invoice", json={"service_code": "dating_vip", "use_balance": True}, headers=as_user(1))
    client.post("/api/admin/grants", json={"tg_id": "1", "service_code": "dating_vip", "note": "тест"}, headers=ADMIN)
    rep = client.get("/api/admin/reports/money?days=30", headers=ADMIN).json()
    assert rep["total"]["paid"] == 1 and rep["total"]["from_balance"] == 70 and rep["total"]["granted"] == 1
    assert rep["total"]["saved_stars"] == 70 and rep["by_section"]["dating"]["granted"] == 1
    vip = client.get("/api/admin/vip/history", headers=ADMIN).json()
    assert vip["summary"]["activations"] == 2 and vip["summary"]["unique"] == 1 and vip["summary"]["granted"] == 1
    assert any("выдал" in i["note"] for i in vip["items"])
    assert client.get("/api/admin/reports/money").status_code == 401


def test_search_test_explains_places_and_shows_hidden(client):
    lid = make(client, title="Cook in cafe", category="job")
    hid = make(client, title="Cook hidden", category="job", publish=False)
    r = client.get("/api/admin/search-test", params={"q": "cook", "mode": "admin"}, headers=ADMIN).json()
    assert [i["id"] for i in r["items"]] == [lid] and r["items"][0]["why"]
    assert [i["id"] for i in r["not_shown"]] == [hid] and "модерации" in r["not_shown"][0]["why"]
    people = client.get("/api/admin/search-test", params={"q": "cook"}, headers=ADMIN).json()
    assert "not_shown" not in people


def test_ad_lists_block_forbidden_topics_hosts_and_budget_stops_it(client):
    client.put("/api/admin/ads/lists", json={"banned_words": "казино, оружие", "banned_hosts": "bad.com", "allowed_hosts": ""}, headers=ADMIN)
    base = {"title": "Акция", "text": "Скидка", "link": "https://good.com/x"}
    assert client.post("/api/admin/ads", json=base | {"text": "Лучшее казино"}, headers=ADMIN).status_code == 400
    assert client.post("/api/admin/ads", json=base | {"link": "https://shop.bad.com/x"}, headers=ADMIN).status_code == 400
    r = client.post("/api/admin/ads", json=base | {"max_shows": 1}, headers=ADMIN)
    assert r.status_code == 201
    assert len(client.get("/api/ads?organic=10", headers=as_user(1)).json()) == 1
    assert client.get("/api/ads?organic=10", headers=as_user(1)).json() == []        # the budget of one show is spent
    client.put("/api/admin/ads/lists", json={"banned_words": "", "banned_hosts": "", "allowed_hosts": "only.com"}, headers=ADMIN)
    assert client.post("/api/admin/ads", json=base, headers=ADMIN).status_code == 400


def test_staff_gives_and_takes_back_stars_with_history_cap_and_journal(client):
    r = client.post("/api/admin/users/1/stars", json={"stars": 100, "reason": "компенсация"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["balance"] == 100
    pay = client.get("/api/me/payments", headers=as_user(1)).json()[0]
    assert pay["gift"] is True and pay["service"] == "Пополнение баланса" and "компенсация" in pay["note"]
    assert client.get("/api/admin/users/1/card", headers=ADMIN).json()["balance"] == 100
    assert client.post("/api/admin/users/1/stars", json={"stars": -150, "reason": "ошибка"}, headers=ADMIN).status_code == 409   # not below zero
    assert client.post("/api/admin/users/1/stars", json={"stars": -40, "reason": "ошибка"}, headers=ADMIN).json()["balance"] == 60
    assert client.post("/api/admin/users/1/stars", json={"stars": 0, "reason": "ничего"}, headers=ADMIN).status_code == 400
    assert client.post("/api/admin/users/1/stars", json={"stars": 5, "reason": "x"}, headers=ADMIN).status_code == 422          # reason is needed
    assert client.post("/api/admin/users/999/stars", json={"stars": 5, "reason": "нет такого"}, headers=ADMIN).status_code == 404
    assert client.post("/api/admin/users/1/stars", json={"stars": 5, "reason": "без входа"}).status_code == 401
    with SessionLocal() as db:
        from app.models import AuditLog
        assert [a.details for a in db.query(AuditLog).filter_by(action="user.stars").order_by(AuditLog.id)] == ["100 компенсация", "-40 ошибка"]


def test_synonyms_and_stop_words_are_edited_in_the_panel(client):
    from app import searchdict
    lid = make(client, title="Studio near subway", category="housing")
    other = make(client, title="Flat on the hill", category="housing")
    assert [x["id"] for x in client.get("/api/catalog", params={"q": "apartment"}).json()["items"]] == []
    client.put("/api/admin/search-dict", json={"synonyms": "apartment = studio, flat\nработа = job", "stopwords": "the, in"}, headers=ADMIN)
    searchdict.reset()
    found = {x["id"] for x in client.get("/api/catalog", params={"q": "apartment"}).json()["items"]}
    assert found == {lid, other}                                                         # «apartment» now means studio or flat
    assert {x["id"] for x in client.get("/api/catalog", params={"q": "in studio"}).json()["items"]} == {lid, other}   # «in» is skipped, studio = flat
    assert {x["id"] for x in client.get("/api/catalog", params={"q": "the subway"}).json()["items"]} == {lid}        # «the» is not needed in the text
    assert client.get("/api/admin/search-dict", headers=ADMIN).json()["stopwords"] == "the, in"
    assert client.get("/api/admin/search-dict").status_code == 401
    client.put("/api/admin/search-dict", json={"synonyms": "", "stopwords": ""}, headers=ADMIN)
    searchdict.reset()


def test_complaints_push_a_listing_down_inside_its_group(client):
    first = make(client, title="Cook one", category="job", user=1)
    second = make(client, title="Cook two", category="job", user=2)       # newer: normally first
    assert [x["id"] for x in client.get("/api/catalog", params={"q": "cook"}).json()["items"]] == [second, first]
    for u in (1, 3):
        client.post("/api/reports", json={"target_type": "listing", "target_id": str(second), "reason": "fake"}, headers=as_user(u))
    ids = [x["id"] for x in client.get("/api/catalog", params={"q": "cook"}).json()["items"]]
    assert ids == [first, second]                                          # two different people complained: it sinks


def test_search_log_keeps_no_personal_data_and_shows_empty_queries(client):
    make(client, title="Cook in cafe", category="job")
    client.get("/api/catalog", params={"q": "cook"})
    client.get("/api/catalog", params={"q": "welder +82 10 1234 5678 ivan@mail.com @ivan_kr"})
    client.get("/api/catalog", params={"q": "welder"})
    log = client.get("/api/admin/search-log?days=7", headers=ADMIN).json()
    texts = {x["q"] for x in log["top"]}
    assert "cook" in texts and "welder" in texts
    assert not any("ivan" in t or "1234" in t or "@" in t for t in texts)                    # cleaned before saving
    assert any("<номер>" in t and "<email>" in t for t in texts)
    assert {x["q"] for x in log["empty"]} >= {"welder"}
    assert client.get("/api/admin/search-log/export", headers=ADMIN).status_code == 200      # the test admin is the super administrator
    assert client.get("/api/admin/search-log?days=7").status_code == 401
    assert client.post("/api/admin/search/reindex", headers=ADMIN).status_code == 200


def test_ab_test_alternates_and_picks_a_winner(client):
    from app import ads
    r = client.post("/api/admin/ads", json={"title": "Вариант А", "text": "a", "link": "https://good.com/x", "title_b": "Вариант Б", "text_b": "b"}, headers=ADMIN)
    aid = r.json()["id"]
    seen = [client.get("/api/ads?organic=10", headers=as_user(1)).json()[0]["ad"] for _ in range(4)]
    assert [s["variant"] for s in seen] == ["a", "b", "a", "b"] and seen[1]["title"] == "Вариант Б"
    with SessionLocal() as db:
        ad = db.get(ads.Ad, aid)
        ad.shows, ad.shows_b, ad.clicks, ad.clicks_b = 400, 200, 30, 20                       # A: 10/200 clicks 5%, B: 20/200 = 10%
        db.commit()
    assert client.post(f"/api/ads/{aid}/click?v=b").status_code == 204
    row = client.get("/api/admin/ads", headers=ADMIN).json()[0]
    assert row["ab"]["winner"] == "b"
    assert client.get("/api/ads?organic=10", headers=as_user(1)).json()[0]["ad"]["variant"] == "b"       # only the winner is shown now


def test_calendar_marks_clashes(client):
    for i in range(5):
        client.post("/api/admin/ads", json={"title": f"Реклама {i}", "link": "https://good.com/x", "place": "list", "days": 10}, headers=ADMIN)
    for t in ("Акция 1", "Акция 2"):
        client.post("/api/admin/campaigns", json={"title": t, "service_code": "dating_vip", "audience": "all", "last_days": 5}, headers=ADMIN)
    cal = client.get("/api/admin/calendar", headers=ADMIN).json()
    assert len(cal) == 7
    assert any("мест" in c for i in cal if i["kind"] == "ad" for c in i["conflicts"])
    assert any("пересекается" in c for i in cal if i["kind"] == "promo" for c in i["conflicts"])
