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
