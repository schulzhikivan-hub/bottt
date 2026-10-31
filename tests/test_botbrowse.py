import pytest
from sqlalchemy import select

from app import botadmin as ba
from app import botbrowse as bb
from app import payments
from app.config import settings
from app.database import SessionLocal, engine
from app.models import (Admin, Article, DatingProfile, Favorite, Group, Listing, User)
from tests.tg import reset_db

USER = 4242


@pytest.fixture()
def db(tmp_path, monkeypatch):
    assert engine.url.drivername == "sqlite"
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    reset_db()
    bb.STATE.clear()
    bb.WAIT_QUERY.clear()
    ba.PENDING_INPUT.clear()
    with SessionLocal() as session:
        session.add(User(tg_id=str(USER), consent_at=payments.now()))
        session.add(Admin(tg_id="555", role="moderator", active=True, created_at=payments.now()))
        session.commit()
        yield session


def add(db, title, category="job", status="approved", **kw):
    item = Listing(category=category, title=title, city=kw.pop("city", "Сеул"), description="Описание",
                   contact=kw.pop("contact", "@hr_asan"), author_telegram_id="1", status=status,
                   published_at=payments.now(), **kw)
    db.add(item)
    db.commit()
    return item


def test_section_shows_card_inside_the_chat(db):
    add(db, "Повар в Сеуле", price_amount=2_500_000, schedule="day", housing_provided=True)
    view = bb.act(db, USER, "b:s:j")
    assert "Повар в Сеуле" in view.text and "2 500 000" in view.text and "жильё" in view.text
    assert any(b.get("d") == "b:n" for row in view.rows for b in row)
    assert any("url" in b and b["url"] == "https://t.me/hr_asan" for row in view.rows for b in row)


def test_next_previous_wrap_and_only_published(db):
    add(db, "Первая"); add(db, "Вторая"); add(db, "Черновик", status="draft")
    bb.act(db, USER, "b:s:j")
    texts = [bb.act(db, USER, "b:n").text for _ in range(3)]
    assert sum("Вторая" in t or "Первая" in t for t in texts) == 3 and not any("Черновик" in t for t in texts)


def test_cars_and_services_are_separate_sections(db):
    add(db, "Соната", category="marketplace", goods_category="auto", details={"year": 2018, "mileage_km": 85000})
    add(db, "Перевод", category="marketplace", goods_category="services", details={"service_area": "Сеул"})
    add(db, "Диван", category="marketplace", goods_category="furniture")
    car = bb.act(db, USER, "b:s:a").text
    assert "Соната" in car and "2018" in car and "85 000 км" in car and "Диван" not in car
    assert "Перевод" in bb.act(db, USER, "b:s:s").text
    assert "Диван" in bb.act(db, USER, "b:s:m").text or "Соната" in bb.act(db, USER, "b:s:m").text


def test_region_filter_search_and_reset(db):
    add(db, "Сеульская", city="Сеул, Каннам", region_code="seoul")
    add(db, "Пусанская", city="Пусан", region_code="busan")
    bb.act(db, USER, "b:s:j")
    view = bb.act(db, USER, "b:rr:busan")
    assert "Пусанская" in view.text and "Сеульская" not in view.text
    assert len(bb.act(db, USER, "b:r").rows) >= 6  # the region picker
    bb.act(db, USER, "b:z")
    bb.act(db, USER, "b:q")
    assert USER in bb.WAIT_QUERY
    typed = bb.apply_query(db, USER, "сеульская")
    assert "Сеульская" in typed.text and "Пусанская" not in typed.text
    assert bb.apply_query(db, USER, "ещё") is None  # no longer waiting


def test_favorite_toggles_and_needs_consent(db):
    item = add(db, "Любимая")
    bb.act(db, USER, "b:s:j")
    bb.act(db, USER, f"b:f:{item.id}")
    assert db.scalar(select(Favorite).where(Favorite.user_tg_id == str(USER))) is not None
    bb.act(db, USER, f"b:f:{item.id}")
    assert db.scalar(select(Favorite).where(Favorite.user_tg_id == str(USER))) is None
    stranger = bb.act(db, 7, f"b:f:{item.id}")
    assert "правила" in stranger.text


def test_dating_in_chat_like_and_match(db):
    db.add(User(tg_id="8", consent_at=payments.now()))
    db.add(DatingProfile(owner_tg_id="8", birth_year=1995, gender="f", city="Сеул", bio="Привет", status="published", photo_key="p.jpg"))
    db.commit()
    view = bb.act(db, USER, "b:dt")
    assert "Привет" in view.text
    pid = db.scalar(select(DatingProfile.id))
    liked = bb.act(db, USER, f"b:dl:{pid}")
    assert liked.alert == "Отправлено" and "Привет" not in liked.text
    from app.models import DatingLike
    db.add(DatingLike(from_tg="8", to_tg=str(USER)))
    db.add(DatingProfile(owner_tg_id=str(USER), birth_year=1994, gender="m", city="Сеул", bio="Я", status="published", photo_key="q.jpg"))
    db.add(User(tg_id="9", consent_at=payments.now()))
    db.add(DatingProfile(owner_tg_id="9", birth_year=1990, gender="f", city="Сеул", bio="Другая", status="published", photo_key="r.jpg"))
    db.commit()
    bb.act(db, USER, "b:dt")
    p9 = db.scalar(select(DatingProfile.id).where(DatingProfile.owner_tg_id == "9"))
    assert bb.act(db, USER, f"b:dl:{p9}").alert == "Отправлено"
    assert "Взаимная" not in bb.act(db, USER, "b:dt").alert


def test_dating_requires_consent(db):
    assert "правила" in bb.act(db, 31337, "b:dt").text


def test_community_and_guide(db):
    db.add(Group(name="Узбеки в Корее", flag="🇺🇿", telegram_link="https://t.me/uz_korea", description="Чат"))
    db.add(Article(section="visas", title="Продление E-9", body="Текст", published=True, checked_at=payments.now()))
    db.add(Article(section="visas", title="Черновик", body="x", published=False))
    db.commit()
    community = bb.act(db, USER, "b:cm")
    assert "Узбеки" in community.text and any(b.get("url") == "https://t.me/uz_korea" for r in community.rows for b in r)
    section = bb.act(db, USER, "b:ga:visas")
    assert "Продление E-9" in str(section.rows) and "Черновик" not in str(section.rows)
    aid = db.scalar(select(Article.id).where(Article.title == "Продление E-9"))
    assert "Текст" in bb.act(db, USER, f"b:gr:{aid}").text


def test_menu_has_sections_and_no_forced_redirect(db):
    from app.bot import menu_markup
    buttons = [b for row in menu_markup(False).inline_keyboard for b in row]
    labels = [b.text for b in buttons]
    for name in ("Работа", "Жильё", "Барахолка", "Знакомства", "Сообщество"):
        assert any(name in t for t in labels)
    sections = [b for b in buttons if b.callback_data and b.callback_data.startswith("b:")]
    assert len(sections) >= 8  # sections are callbacks, not links to the Mini App


# ---- admin: new moderation features ----

def test_admin_can_find_hide_and_show_a_listing(db):
    item = add(db, "Скрыть меня", category="marketplace", goods_category="auto", details={"model": "Sonata", "year": 2018})
    card = ba.handle(db, 555, "moderator", ["a", "L", "find"])
    assert card.state and card.state["kind"] == "find_listing"
    ba.PENDING_INPUT[555] = card.state
    found = ba.handle_text(db, 555, "moderator", str(item.id))
    assert "Sonata" in found.text and "2018" in found.text
    assert any("hide" in d for row in found.rows for _, d in row)
    ask = ba.handle(db, 555, "moderator", ["a", "L", "hide", str(item.id)])
    assert ask.state["kind"] == "listing_hide"
    ba.PENDING_INPUT[555] = ask.state
    done = ba.handle_text(db, 555, "moderator", "Подозрительное объявление")
    db.refresh(item)
    assert item.status == "hidden" and item.note == "Подозрительное объявление"
    assert done.notify and "скрыто" in done.notify[0][1]
    back = ba.handle(db, 555, "moderator", ["a", "L", "show", str(item.id)])
    db.refresh(item)
    assert item.status == "approved" and back.notify


def test_hide_needs_reason_and_published_state(db):
    from app import admin_ops as ops
    item = add(db, "Тест")
    with pytest.raises(ops.OpError):
        ops.moderate_listing(db, "1", item.id, "hide", "")
    pending = add(db, "Ждёт", status="pending")
    with pytest.raises(ops.OpError):
        ops.moderate_listing(db, "1", pending.id, "hide", "причина")
    with pytest.raises(ops.OpError):
        ops.moderate_listing(db, "1", item.id, "show")


def test_web_admin_sees_photos_author_and_can_find(db):
    from fastapi.testclient import TestClient
    from app.main import app
    item = add(db, "Видно всё", status="pending", category="housing")
    with TestClient(app) as c:
        h = {"X-Admin-Session": "test-session-token"}
        queue = c.get("/api/admin/listings", headers=h).json()
        assert queue[0]["author_id"] == "1" and "photos" in queue[0]
        one = c.get(f"/api/admin/listings/{item.id}", headers=h).json()
        assert one["title"] == "Видно всё"
        assert c.get("/api/admin/listings/99999", headers=h).status_code == 404
        assert c.post(f"/api/admin/listings/{item.id}/hide?reason=abc", headers=h).status_code == 409
