import pytest
from fastapi.testclient import TestClient

from app.database import engine
from app.main import app
from tests.tg import accept_terms, as_user, reset_db


@pytest.fixture()
def client():
    assert engine.url.drivername == "sqlite"
    reset_db()
    with TestClient(app) as c:
        for uid in (1, 2, 3):
            accept_terms(c, uid)
        yield c


def test_blocked_list_never_shows_other_telegram_ids(client):
    client.post("/api/users/2/block", headers=as_user(1))
    client.post("/api/users/3/block", headers=as_user(1))
    rows = client.get("/api/me/blocks", headers=as_user(1)).json()
    assert [r["label"] for r in rows] == ["Пользователь №1", "Пользователь №2"]
    assert "2" not in str([r for r in rows if r["label"].endswith("1")]).replace("№1", "")
    assert all(set(r) == {"id", "label"} for r in rows)


def test_unblock_by_entry_only_own(client):
    client.post("/api/users/2/block", headers=as_user(1))
    entry = client.get("/api/me/blocks", headers=as_user(1)).json()[0]["id"]
    assert client.delete(f"/api/me/blocks/{entry}", headers=as_user(3)).status_code == 404
    assert client.delete(f"/api/me/blocks/{entry}", headers=as_user(1)).status_code == 200
    assert client.get("/api/me/blocks", headers=as_user(1)).json() == []
