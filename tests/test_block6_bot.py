import pytest
from sqlalchemy import select

from app import adminx
from app import botadmin as ba
from app.config import settings
from app.database import SessionLocal, engine
from app.models import (Admin, Application, Article, Banner, Broadcast, Group, Listing, SavedSearch, User)
from app.payments import now
from tests.tg import reset_db

MOD, ADMIN, SUPPORT = 555, 777, 888


@pytest.fixture()
def db(tmp_path, monkeypatch):
    assert engine.url.drivername == "sqlite"
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    reset_db()
    ba.PENDING_INPUT.clear()
    with SessionLocal() as s:
        for tg, role in ((MOD, "moderator"), (ADMIN, "admin"), (SUPPORT, "support")):
            s.add(Admin(tg_id=str(tg), role=role, active=True, created_at=now()))
        s.commit()
        yield s


def go(db, who, *parts):
    return ba.handle(db, who, ba.role_of(db, who), ["a", *parts])


def say(db, who, text):
    return ba.handle_text(db, who, ba.role_of(db, who), text)


def pending(db, title="Диван"):
    item = Listing(category="marketplace", title=title, city="Сеул", description="d", contact="@x1234", author_telegram_id="42", status="pending")
    db.add(item)
    db.commit()
    return item


def test_reject_with_template_in_one_tap(db):
    item = pending(db)
    menu = go(db, MOD, "L", "no", str(item.id))
    assert len(menu.rows) == len(adminx.REASON_TEMPLATES) + 2 and "Выберите причину" in menu.text
    assert any(d == f"a:L:nox:{item.id}" for row in menu.rows for _, d in row)       # "write your own" is still there
    done = go(db, MOD, "L", "not", str(item.id), "1")                                # "no prepayment" template
    db.refresh(item)
    assert item.status == "rejected" and "предоплату" in item.note
    assert done.notify[0][0] == "42" and "предоплату" in done.notify[0][1]
    assert go(db, MOD, "L", "not", str(item.id), "99").alert == "Шаблон не найден"


def test_needs_fix_with_template_and_custom(db):
    a, b = pending(db, "Первое"), pending(db, "Второе")
    go(db, MOD, "L", "fixt", str(a.id), "0")
    db.refresh(a)
    assert a.status == "needs_fix" and "фото" in a.note
    ask = go(db, MOD, "L", "fixx", str(b.id))
    assert ask.state["kind"] == "listing_fix"
    ba.PENDING_INPUT[MOD] = ask.state
    say(db, MOD, "Добавьте график работы")
    db.refresh(b)
    assert b.status == "needs_fix" and b.note == "Добавьте график работы"


def test_support_role_sees_only_reports_and_users(db):
    menu = go(db, SUPPORT, "menu")
    labels = str(menu.rows)
    assert "Жалобы" in labels and "Пользователи" in labels and "Объявления" not in labels and "Платежи" not in labels
    assert go(db, SUPPORT, "L").alert                                  # no moderation of listings
    assert go(db, SUPPORT, "P").alert and go(db, SUPPORT, "B").alert and go(db, SUPPORT, "T").alert
    assert go(db, SUPPORT, "R").text and not go(db, SUPPORT, "R").alert
    ask = go(db, SUPPORT, "U")
    assert ask.state["kind"] == "find_user"


def test_moderator_has_no_admin_sections(db):
    for section in ("N", "B", "W", "G", "K", "S", "P", "C"):
        assert go(db, MOD, section).alert, section
    assert "Промокоды" in str(go(db, ADMIN, "menu").rows) and "Рассылка" in str(go(db, ADMIN, "menu").rows)


def test_user_card_shows_activity(db):
    db.add(User(tg_id="2", consent_at=now()))
    db.add(Listing(category="job", title="Повар", city="Сеул", description="d", author_telegram_id="1", status="approved", contact=""))
    db.commit()
    lid = db.scalar(select(Listing.id))
    db.add(Application(listing_id=lid, applicant_tg_id="2", contact="@a1234", status="new"))
    db.add(SavedSearch(user_tg_id="2", name="Работа · повар", params={"q": "повар"}))
    db.commit()
    card = ba.user_card(db, "2")
    assert "Отклики: 1" in card.text and "Работа · повар" in card.text and "Поиски под наблюдением: 1" in card.text


def test_analytics_view(db):
    assert "За 30 дней" in go(db, ADMIN, "N").text and "Воронка" in go(db, ADMIN, "N").text


def test_broadcast_flow_in_the_bot(db):
    for i in range(5):
        db.add(User(tg_id=str(4000 + i), consent_at=now(), region_code="busan"))
    db.add(User(tg_id="4100", consent_at=now(), region_code="seoul"))
    db.commit()
    ask = go(db, ADMIN, "B", "new")
    ba.PENDING_INPUT[ADMIN] = ask.state
    assert "до 500" in say(db, ADMIN, "ab").text
    preview = say(db, ADMIN, "Скоро новые вакансии!")
    assert "получателей: 6" in preview.text and "Отправить?" in preview.text
    bc = db.scalar(select(Broadcast))
    region = go(db, ADMIN, "B", "rc", str(bc.id), "busan")
    assert "получателей: 5" in region.text
    started = go(db, ADMIN, "B", "go", str(bc.id))
    assert "запущена" in started.text
    assert go(db, ADMIN, "B", "go", str(bc.id)).alert                     # a second start is refused
    stopped = go(db, ADMIN, "B", "no", str(bc.id))
    assert "Остановлено" in stopped.text
    assert go(db, ADMIN, "B").text.startswith("📣 Рассылки")


def test_banner_flow_in_the_bot(db):
    ask = go(db, ADMIN, "W", "new")
    ba.PENDING_INPUT[ADMIN] = ask.state
    assert "Не разобрал" in say(db, ADMIN, "Баннер | https://t.me/x | много").text
    assert "https://" in say(db, ADMIN, "Баннер | javascript:evil | 3").text and ba.PENDING_INPUT.get(ADMIN)
    done = say(db, ADMIN, "Новые вакансии в Пусане | https://t.me/koreahub | 7")
    assert "добавлен" in done.text
    b = db.scalar(select(Banner))
    assert b.link == "https://t.me/koreahub" and b.ends_at is not None
    go(db, ADMIN, "W", "t", str(b.id))
    db.refresh(b)
    assert b.is_active is False
    go(db, ADMIN, "W", "d", str(b.id))
    assert db.scalar(select(Banner)) is None


def test_content_flow_groups_and_articles(db):
    g = db.scalar(select(Group))
    ask = go(db, ADMIN, "G", "l", str(g.id))
    ba.PENDING_INPUT[ADMIN] = ask.state
    assert "https://t.me/" in say(db, ADMIN, "http://evil.example").text
    say(db, ADMIN, "https://t.me/uz_korea")
    db.refresh(g)
    assert g.telegram_link == "https://t.me/uz_korea"
    ba.PENDING_INPUT[ADMIN] = go(db, ADMIN, "G", "m", str(g.id)).state
    assert "число" in say(db, ADMIN, "много").text
    say(db, ADMIN, "1500")
    db.refresh(g)
    assert g.members_count == 1500
    art = db.scalar(select(Article))
    assert go(db, ADMIN, "G", "p", str(art.id)).alert                     # no source yet
    ba.PENDING_INPUT[ADMIN] = go(db, ADMIN, "G", "s", str(art.id)).state
    say(db, ADMIN, "https://www.hikorea.go.kr/info")
    db.refresh(art)
    assert art.source_url.startswith("https://www.hikorea") and art.checked_at is not None
    assert "нет текста" in go(db, ADMIN, "G", "p", str(art.id)).alert     # a source alone is not enough: the text must exist
    art.body = "Текст статьи"
    db.commit()
    go(db, ADMIN, "G", "p", str(art.id))
    db.refresh(art)
    assert art.published is True
    assert go(db, MOD, "G").alert
