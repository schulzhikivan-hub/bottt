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
