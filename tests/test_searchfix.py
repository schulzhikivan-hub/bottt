from app import searchfix
from app.catalog import search
from app.database import SessionLocal
from app.models import Listing
from app.payments import now
from tests.tg import reset_db
import datetime as dt


def test_variants():
    assert searchfix.variants("seoul")[:2] == ["seoul", "сеул"]
    assert "сварщик" in searchfix.variants("svarschik") or "сварщик" in [searchfix.translit("svarshchik")]
    assert "повар" in searchfix.variants("gjdfh")        # typed on the wrong layout
    assert searchfix.variants("сварщика")[-1] == "сварщи"


def make(db, title, city="Сеул"):
    row = Listing(author_telegram_id="1", category="job", title=title, description="Описание вакансии на заводе", city=city,
                  city_key=city.lower(), status="approved", contact="c", expires_at=now() + dt.timedelta(days=5))
    row.search_text = f"{title} Описание вакансии на заводе {city}".lower()
    db.add(row)
    db.commit()


def test_search_forgives_layout_translit_ending_and_typo():
    reset_db()
    with SessionLocal() as db:
        make(db, "Сварщик на завод")
        make(db, "Повар в ресторан", "Пусан")
        assert [x.title for x in search(db, q="сварщика")[0]] == ["Сварщик на завод"]       # ending
        assert [x.title for x in search(db, q="busan")[0]] == ["Повар в ресторан"]           # latin city
        assert [x.title for x in search(db, q="gjdfh")[0]] == ["Повар в ресторан"]           # wrong layout
        assert [x.title for x in search(db, q="сварщег")[0]] == ["Сварщик на завод"]         # typo
        assert search(db, q="космонавт")[0] == []                                           # no false hits


def test_daily_digest_waits_and_merges():
    import datetime as dt
    from app import searches
    from app.models import SavedSearch, User
    reset_db()
    with SessionLocal() as db:
        db.add(User(tg_id="9", notify_prefs={"searches_digest": True}))
        past = now() - dt.timedelta(hours=2)
        db.add(SavedSearch(user_tg_id="9", name="Повар", params={"q": "повар"}, last_checked_at=past))
        db.add(SavedSearch(user_tg_id="9", name="Сварщик", params={"q": "сварщик"}, last_checked_at=past))
        db.commit()
        make(db, "Повар в ресторан")
        make(db, "Сварщик на завод")
        for x in db.query(Listing).all():
            x.published_at = now()
        db.commit()
        assert searches.new_matches(db) == []                       # only 2 h since the last check: wait
        for s in db.query(SavedSearch).all():
            s.last_checked_at = now() - dt.timedelta(hours=25)
        for x in db.query(Listing).all():
            x.published_at = now() - dt.timedelta(hours=3)
        db.commit()
        out = searches.new_matches(db)
        assert len(out) == 1 and "Повар" in out[0][1] and "Сварщик" in out[0][1]


def test_renew_adds_to_what_is_left_and_explains_when_too_early():
    from fastapi.testclient import TestClient
    from app.main import app
    from tests.tg import accept_terms, as_user
    reset_db()
    c = TestClient(app)
    accept_terms(c, 1)
    with SessionLocal() as db:
        make(db, "Повар в ресторан")
        item = db.query(Listing).one(); item.author_telegram_id = "1"
        item.expires_at = now() + dt.timedelta(days=20); db.commit(); lid = item.id
    left = lambda: (dt.datetime.fromisoformat(c.get("/api/me/listings", headers=as_user(1)).json()[0]["expires_at"]).replace(tzinfo=dt.timezone.utc) - now()).days
    assert c.post(f"/api/listings/{lid}/renew", headers=as_user(1)).status_code == 200
    assert 48 <= left() <= 50                                   # 20 days left + 30, not just 30
    again = c.post(f"/api/listings/{lid}/renew", headers=as_user(1))
    assert again.status_code == 409 and "Продлить можно с" in again.json()["detail"]
    with SessionLocal() as db:
        db.query(Listing).one().expires_at = now() - dt.timedelta(days=3); db.query(Listing).one().status = "expired"; db.commit()
    assert c.post(f"/api/listings/{lid}/renew", headers=as_user(1)).status_code == 200
    assert 28 <= left() <= 30                                   # an ended listing starts again from today


def test_saved_search_no_duplicates_pause_and_exact_alerts_only():
    from fastapi.testclient import TestClient
    from app.main import app
    from app import searches
    from tests.tg import accept_terms, as_user
    reset_db()
    c = TestClient(app)
    accept_terms(c, 1)
    first = c.post("/api/me/searches", json={"q": "сварщик"}, headers=as_user(1))
    assert first.status_code == 201
    assert c.post("/api/me/searches", json={"q": "сварщик"}, headers=as_user(1)).status_code == 409
    sid = first.json()["id"]
    assert c.post(f"/api/me/searches/{sid}/pause", json={"on": True}, headers=as_user(1)).json()["paused"] is True
    with SessionLocal() as db:
        make(db, "Сварщик на завод")
        for x in db.query(Listing).all():
            x.published_at = now()
        db.commit()
        from app.models import SavedSearch
        db.query(SavedSearch).one().last_checked_at = now() - dt.timedelta(hours=1); db.commit()
        assert searches.new_matches(db) == []                         # paused: silent
    c.post(f"/api/me/searches/{sid}/pause", json={"on": False}, headers=as_user(1))
    with SessionLocal() as db:
        db.query(SavedSearch).one().last_checked_at = now() - dt.timedelta(hours=1)
        make(db, "Сварщик на другой завод")                      # appears after the search was resumed
        for x in db.query(Listing).all():
            x.author_telegram_id = "2"                            # own listings never alert their author
            x.published_at = now()
        db.commit()
        out = searches.new_matches(db)
        assert len(out) == 1 and "Сварщик" in out[0][1]


def test_typo_hint_and_client_error_log():
    from fastapi.testclient import TestClient
    from app.main import app
    from tests.tg import TEST_ADMIN_SESSION
    reset_db()
    c = TestClient(app)
    with SessionLocal() as db:
        make(db, "Сварщик на завод")
    got = c.get("/api/catalog?q=сваршик").json()
    assert got["total"] == 1 and got["corrected"] == "сварщик"
    assert "corrected" not in c.get("/api/catalog?q=сварщик").json()
    assert c.post("/api/client-log", json={"where": "/api/listings/5/archive", "status": 500, "message": "boom", "request_id": "abc123"}).status_code == 204
    rows = c.get("/api/admin/client-errors", headers={"X-Admin-Session": TEST_ADMIN_SESSION}).json()
    assert rows[0]["request_id"] == "abc123" and rows[0]["status"] == 500
