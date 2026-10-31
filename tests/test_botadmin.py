import pytest
from sqlalchemy import select

from app import botadmin as ba
from app import lifecycle
from app import payments
from app.config import settings
from app.database import SessionLocal, engine
from app.models import (Admin, AuditLog, DatingProfile, Listing, ListingPhoto, Payment, Pricing, Report, User)
from tests.tg import reset_db

SUPER, MODERATOR, ADMIN_ID = 999, 555, 777


@pytest.fixture()
def db(tmp_path, monkeypatch):
    assert engine.url.drivername == "sqlite", "Tests must run on SQLite only"
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    reset_db()  # creates super administrator 999
    ba.PENDING_INPUT.clear()
    with SessionLocal() as session:
        session.add(Admin(tg_id=str(MODERATOR), role="moderator", active=True, created_at=payments.now()))
        session.add(Admin(tg_id=str(ADMIN_ID), role="admin", active=True, created_at=payments.now()))
        session.commit()
        yield session


def listing(db, title="Диван", author="42", status="pending", category="marketplace"):
    item = Listing(category=category, title=title, city="Сеул", price="1 000 ₩", description="Хороший",
                   contact="@seller", author_telegram_id=author, status=status)
    db.add(item)
    db.commit()
    return item


def press(db, who, *parts):
    role = ba.role_of(db, who)
    return ba.handle(db, who, role, ["a", *parts])


def texts(reply):
    return reply.text + " " + " ".join(label for row in reply.rows for label, _ in row)


# ---- access and menu ----

def test_unknown_person_has_no_role(db):
    assert ba.role_of(db, 31337) is None
    db.get(Admin, str(MODERATOR)).active = False
    db.commit()
    assert ba.role_of(db, MODERATOR) is None


def test_menu_depends_on_role(db):
    mod = texts(press(db, MODERATOR, "menu"))
    assert "Объявления" in mod and "Статистика" not in mod and "Сотрудники" not in mod
    adm = texts(press(db, ADMIN_ID, "menu"))
    assert "Статистика" in adm and "Сотрудники" not in adm
    sup = texts(press(db, SUPER, "menu"))
    assert "Статистика" in sup and "Сотрудники" in sup


def test_menu_shows_queue_counts(db):
    listing(db)
    listing(db, title="Стол")
    assert "Объявления (2)" in texts(press(db, MODERATOR, "menu"))


def test_moderator_cannot_open_money_or_staff_sections(db):
    for section in ("S", "P", "C", "J", "T"):
        reply = press(db, MODERATOR, section)
        assert reply.alert and not reply.text, section


# ---- listings ----

def test_listing_queue_shows_card_and_buttons(db):
    item = listing(db)
    reply = press(db, MODERATOR, "L")
    assert f"#{item.id}" in reply.text and "Диван" in reply.text and "Автор: 42" in reply.text
    labels = texts(reply)
    assert "Одобрить" in labels and "Доработать" in labels and "Отклонить" in labels


def test_empty_queue(db):
    assert "пуста" in press(db, MODERATOR, "L").text


def test_approve_publishes_notifies_author_and_is_journaled(db):
    item = listing(db)
    reply = press(db, MODERATOR, "L", "ok", str(item.id))
    db.refresh(item)
    assert item.status == "approved"
    assert ("42", "Ваше объявление «Диван»: одобрено и опубликовано.") in reply.notify
    entry = db.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
    assert entry.actor == str(MODERATOR) and entry.action == "listing.approve"


def test_approving_moves_to_next_listing(db):
    first, second = listing(db, title="Первое"), listing(db, title="Второе")
    reply = press(db, MODERATOR, "L", "ok", str(first.id))
    assert "Второе" in reply.text and f"#{second.id}" in reply.text


def test_reject_needs_reason_then_notifies_author_with_it(db):
    item = listing(db)
    ask = press(db, MODERATOR, "L", "no", str(item.id))
    assert ask.state == {"kind": "listing_no", "id": item.id}
    assert ba.PENDING_INPUT[MODERATOR]["kind"] == "listing_no"
    too_short = ba.handle_text(db, MODERATOR, "moderator", "no")
    assert "Не получилось" in too_short.text
    assert ba.PENDING_INPUT[MODERATOR]["kind"] == "listing_no"  # still waiting for a proper reason
    done = ba.handle_text(db, MODERATOR, "moderator", "Контакты в описании")
    db.refresh(item)
    assert item.status == "rejected" and item.note == "Контакты в описании"
    assert any("Контакты в описании" in text for _, text in done.notify)
    assert MODERATOR not in ba.PENDING_INPUT


def test_return_for_fix_sets_status_and_comment(db):
    item = listing(db)
    press(db, MODERATOR, "L", "fix", str(item.id))
    ba.handle_text(db, MODERATOR, "moderator", "Добавьте цену")
    db.refresh(item)
    assert item.status == "needs_fix" and item.note == "Добавьте цену"


def test_text_without_question_is_ignored(db):
    assert ba.handle_text(db, MODERATOR, "moderator", "привет") is None


def test_cancel_clears_waiting(db):
    item = listing(db)
    press(db, MODERATOR, "L", "no", str(item.id))
    ba.PENDING_INPUT.pop(MODERATOR)
    assert ba.handle_text(db, MODERATOR, "moderator", "причина") is None
    db.refresh(item)
    assert item.status == "pending"


# ---- dating ----

def test_dating_queue_requires_photo_to_publish(db):
    db.add(DatingProfile(owner_tg_id="10", birth_year=1995, gender="f", city="Сеул", bio="Привет",
                         photo_key=None, status="pending"))
    db.commit()
    reply = press(db, MODERATOR, "D")
    assert "Фото не загружено" in reply.text and "Опубликовать" not in texts(reply)


def test_dating_approve_notifies_owner_and_sends_photo(db, tmp_path):
    root = ba.upload_root()
    (root / "p.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 50)
    profile = DatingProfile(owner_tg_id="10", birth_year=1995, gender="f", city="Сеул", bio="Привет",
                            photo_key="p.jpg", status="pending")
    db.add(profile)
    db.commit()
    shown = press(db, MODERATOR, "D")
    assert shown.photo is not None and "Опубликовать" in texts(shown)
    done = press(db, MODERATOR, "D", "ok", str(profile.id))
    db.refresh(profile)
    assert profile.status == "published"
    assert ("10", "Ваша анкета знакомств опубликована.") in done.notify


# ---- reports ----

def test_report_resolve_hides_listing(db):
    item = listing(db, status="approved")
    db.add(Report(reporter_tg_id="7", target_type="listing", target_id=str(item.id), reason="fraud"))
    db.commit()
    report = db.scalar(select(Report).limit(1))
    shown = press(db, MODERATOR, "R")
    assert "fraud" in shown.text and "Диван" in shown.text
    press(db, MODERATOR, "R", "ok", str(report.id))
    db.refresh(item)
    assert item.status == "hidden"


def test_report_decided_twice_gives_alert(db):
    db.add(Report(reporter_tg_id="7", target_type="user", target_id="42", reason="spam"))
    db.commit()
    report = db.scalar(select(Report).limit(1))
    press(db, MODERATOR, "R", "no", str(report.id))
    again = press(db, MODERATOR, "R", "no", str(report.id))
    assert "уже рассмотрена" in again.alert


# ---- employers ----

def test_employer_approval_notifies_applicant(db):
    db.add(User(tg_id="88", employer_status="pending", company_name="Samin Motors",
                registration_no="123-45", contact_person="Ким"))
    db.commit()
    assert "Samin Motors" in press(db, MODERATOR, "E").text
    reply = press(db, MODERATOR, "E", "ok", "88")
    assert db.get(User, "88").employer_status == "approved"
    assert ("88", "Статус работодателя подтверждён.") in reply.notify


# ---- users ----

def test_find_and_block_user(db):
    db.add(User(tg_id="42"))
    db.commit()
    ask = press(db, MODERATOR, "U")
    assert ask.state == {"kind": "find_user"}
    card = ba.handle_text(db, MODERATOR, "moderator", "42")
    assert "Пользователь 42" in card.text and "Заблокировать" in texts(card)
    press(db, MODERATOR, "U", "blk", "42")
    assert "Напишите причину" in ba.PENDING_INPUT[MODERATOR]["kind"] or ba.PENDING_INPUT[MODERATOR]["kind"] == "block"
    short = ba.handle_text(db, MODERATOR, "moderator", "x")
    assert "Не получилось" in short.text
    done = ba.handle_text(db, MODERATOR, "moderator", "Мошенничество")
    assert db.get(User, "42").blocked is True
    assert "заблокирован" in done.text
    press(db, MODERATOR, "U", "unb", "42")
    ba.handle_text(db, MODERATOR, "moderator", "Ошибка проверки")
    assert db.get(User, "42").blocked is False


def test_unknown_user_id_asks_again(db):
    press(db, MODERATOR, "U")
    reply = ba.handle_text(db, MODERATOR, "moderator", "123456")
    assert "не найден" in reply.text
    assert ba.PENDING_INPUT[MODERATOR]["kind"] == "find_user"


def test_user_id_must_be_a_number(db):
    press(db, MODERATOR, "U")
    assert "число" in ba.handle_text(db, MODERATOR, "moderator", "abc").text


# ---- money and prices ----

def test_stats_view_for_admin(db):
    db.add(User(tg_id="1"))
    db.commit()
    assert "Пользователей: 1" in press(db, ADMIN_ID, "S").text


def test_price_change_by_message_is_validated_and_journaled(db):
    press(db, ADMIN_ID, "C", "p", "job_vip")
    bad = ba.handle_text(db, ADMIN_ID, "admin", "много")
    assert "число" in bad.text
    absurd = ba.handle_text(db, ADMIN_ID, "admin", "0")
    assert "Не получилось" in absurd.text
    ok = ba.handle_text(db, ADMIN_ID, "admin", "120")
    assert "Цена сохранена" in ok.text and "job_vip: 120" in ok.text
    assert db.get(Pricing, "job_vip").stars == 120
    log = db.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
    assert log.action == "pricing.update" and log.actor == str(ADMIN_ID)


def test_service_toggle(db):
    press(db, ADMIN_ID, "C", "t", "job_pin")
    assert db.get(Pricing, "job_pin").is_active is False
    press(db, ADMIN_ID, "C", "t", "job_pin")
    assert db.get(Pricing, "job_pin").is_active is True


def test_refund_asks_confirmation_then_refunds(db, monkeypatch):
    calls = []
    monkeypatch.setattr(payments, "telegram_call", lambda method, payload: calls.append((method, payload)) or True)
    item = listing(db, status="approved", author="42")
    payment = Payment(user_tg_id="42", listing_id=item.id, service_code="marketplace_vip", stars=50,
                      payload="p1", telegram_payment_charge_id="charge-1", status="paid", paid_at=payments.now())
    db.add(payment)
    db.commit()
    assert "Вернуть #" in texts(press(db, ADMIN_ID, "P"))
    confirm = press(db, ADMIN_ID, "P", "rf", str(payment.id))
    assert "Вернуть оплату" in confirm.text and calls == []  # nothing happens before the confirmation
    done = press(db, ADMIN_ID, "P", "rfy", str(payment.id))
    db.refresh(payment)
    assert payment.status == "refunded"
    assert calls == [("refundStarPayment", {"user_id": 42, "telegram_payment_charge_id": "charge-1"})]
    assert ("42", f"Оплата #{payment.id} возвращена, услуга отключена.") in done.notify
    again = press(db, ADMIN_ID, "P", "rfy", str(payment.id))
    assert again.alert  # a second refund is refused


def test_audit_view_lists_actions(db):
    item = listing(db)
    press(db, MODERATOR, "L", "ok", str(item.id))
    text = press(db, ADMIN_ID, "J").text
    assert "listing.approve" in text and str(MODERATOR) in text


# ---- staff ----

def test_super_admin_adds_and_disables_staff(db):
    press(db, SUPER, "T", "add")
    assert ba.PENDING_INPUT[SUPER]["kind"] == "add_staff"
    assert "Формат" in ba.handle_text(db, SUPER, "super_admin", "привет").text
    done = ba.handle_text(db, SUPER, "super_admin", "1234 moderator")
    assert db.get(Admin, "1234").role == "moderator" and "1234 · moderator" in done.text
    press(db, SUPER, "T", "add")
    ba.handle_text(db, SUPER, "super_admin", "1234 off")
    assert db.get(Admin, "1234").active is False


def test_super_admin_cannot_remove_own_access(db):
    press(db, SUPER, "T", "add")
    reply = ba.handle_text(db, SUPER, "super_admin", f"{SUPER} off")
    assert "самого себя" in reply.text
    assert db.get(Admin, str(SUPER)).active is True


def test_moderator_cannot_use_staff_input_even_with_stale_state(db):
    ba.PENDING_INPUT[MODERATOR] = {"kind": "add_staff"}
    reply = ba.handle_text(db, MODERATOR, "moderator", "5555 super_admin")
    assert "Недостаточно прав" in reply.text
    assert db.get(Admin, "5555") is None


# ---- edits of published listings, photos ----

def test_waiting_edit_shows_what_changes_and_approving_tells_the_author(db):
    item = listing(db, status="approved", title="Диван")
    lifecycle.edit(db, item, {"title": "Диван угловой", "price_amount": 90000})
    db.commit()
    shown = press(db, MODERATOR, "L")
    assert "Правка опубликованного" in shown.text
    assert "Название: Диван → Диван угловой" in shown.text and "Сумма" in shown.text
    done = press(db, MODERATOR, "L", "ok", str(item.id))
    db.refresh(item)
    assert item.title == "Диван угловой" and item.has_edit is False
    assert any("изменения одобрены" in text for _, text in done.notify)


def test_rejecting_an_edit_keeps_the_published_listing(db):
    item = listing(db, status="approved", title="Диван")
    lifecycle.edit(db, item, {"title": "Подозрительное"})
    db.commit()
    press(db, MODERATOR, "L", "no", str(item.id))
    done = ba.handle_text(db, MODERATOR, "moderator", "Лишнее слово")
    db.refresh(item)
    assert item.status == "approved" and item.title == "Диван" and item.note == "Лишнее слово"
    assert any("правка отклонена" in text for _, text in done.notify)


def test_edit_counts_in_the_menu(db):
    item = listing(db, status="approved")
    lifecycle.edit(db, item, {"title": "Другое"})
    db.commit()
    assert "Объявления (1)" in texts(press(db, MODERATOR, "menu"))


def test_listing_without_a_required_photo_cannot_be_approved_from_the_bot(db, monkeypatch):
    monkeypatch.setattr(ba.lifecycle.settings, "require_photos", True)
    item = listing(db, category="housing")
    shown = press(db, MODERATOR, "L")
    assert "Нет фото" in shown.text and "Одобрить" not in texts(shown)
    reply = press(db, MODERATOR, "L", "ok", str(item.id))  # a stale button is refused as well
    assert "фото" in reply.alert


def test_moderator_gets_the_photo_with_the_card(db):
    item = listing(db)
    key = "a" * 32
    (ba.photo_root() / f"{key}.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 30)
    db.add(ListingPhoto(listing_id=item.id, key=key, position=0, status="live"))
    db.commit()
    assert press(db, MODERATOR, "L").photo is not None


def test_card_shows_key_facts(db):
    item = Listing(category="job", title="Работа", city="Асан", author_telegram_id="42", status="pending",
                   price_amount=2_800_000, schedule="day", visas=",E-9,F-4,", housing_provided=True,
                   details={"company": "Samin Motors"})
    db.add(item)
    db.commit()
    text = press(db, MODERATOR, "L").text
    assert "2 800 000 ₩" in text and "Samin Motors" in text and "E-9,F-4" in text and "жильё" in text
