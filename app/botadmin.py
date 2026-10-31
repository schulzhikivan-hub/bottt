"""Administration inside the Telegram bot: buttons and short messages, no website.

`handle()` and `handle_text()` return a `Reply` and never talk to Telegram, so they are tested
without a network. The aiogram handlers at the bottom only send what they return.
Permissions follow the roles (moderator, admin, super_admin) and every action is journaled.
"""
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from app import admin_ops as ops
from app import payments
from app.admin_auth import MOD, ADM, SUP, ROLES
from app.audit import log_action
from app.dating import age_of, upload_root
from app.models import (Admin, AuditLog, DatingProfile, Listing, Payment, Pricing, Report, User)

CATEGORY = {"job": "Работа", "housing": "Жильё", "marketplace": "Барахолка"}
BACK = [("⬅️ В меню", "a:menu")]
# What each section needs. The menu is open to every staff member.
LEVEL = {"L": MOD, "D": MOD, "R": MOD, "E": MOD, "U": MOD, "S": ADM, "P": ADM, "C": ADM, "J": ADM, "T": SUP}
# A text answer the bot is waiting for from an administrator: {admin_id: {"kind": ..., ...}}
PENDING_INPUT: dict[int, dict] = {}
INPUT_LEVEL = {"listing_fix": MOD, "listing_no": MOD, "find_user": MOD, "block": MOD, "unblock": MOD,
               "price": ADM, "add_staff": SUP}


@dataclass
class Reply:
    text: str
    rows: list[list[tuple[str, str]]] = field(default_factory=list)
    photo: Path | None = None
    state: dict | None = None            # wait for the administrator's next text message
    notify: list[tuple[str, str]] = field(default_factory=list)  # (telegram id, message) to other people
    alert: str = ""                      # short pop-up instead of a message


def role_of(db: Session, tg_id: int | str) -> str | None:
    admin = db.get(Admin, str(tg_id))
    return admin.role if admin is not None and admin.active else None


def _count(db: Session, model, *conditions) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def _short(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ---------- views ----------

def menu(db: Session, role: str) -> Reply:
    rows = [
        [(f"📋 Объявления ({_count(db, Listing, Listing.status == 'pending')})", "a:L"),
         (f"💗 Анкеты ({_count(db, DatingProfile, DatingProfile.status == 'pending')})", "a:D")],
        [(f"🚩 Жалобы ({_count(db, Report, Report.status == 'new')})", "a:R"),
         (f"🏢 Работодатели ({_count(db, User, User.employer_status == 'pending')})", "a:E")],
        [("👥 Пользователи", "a:U")],
    ]
    if role in ADM:
        rows += [[("📊 Статистика", "a:S"), ("💳 Платежи", "a:P")], [("💰 Цены", "a:C"), ("📜 Журнал", "a:J")]]
    if role in SUP:
        rows.append([("🧑‍💼 Сотрудники", "a:T")])
    return Reply("🛠 Админ-панель KOREA HUB\nВаша роль: " + role + "\nВыберите раздел:", rows)


def listing_queue(db: Session, header: str = "") -> Reply:
    item = db.scalar(select(Listing).where(Listing.status == "pending").order_by(Listing.id).limit(1))
    waiting = _count(db, Listing, Listing.status == "pending")
    if item is None:
        return Reply(header + "Очередь объявлений пуста ✅", [BACK])
    text = (f"{header}📋 Объявление #{item.id} (в очереди: {waiting})\n"
            f"{CATEGORY.get(item.category, item.category)} · {item.city} · {item.price or 'цена не указана'}\n"
            f"Заголовок: {_short(item.title, 160)}\n\n{_short(item.description, 900)}\n\n"
            f"Контакт: {item.contact or '—'}\nАвтор: {item.author_telegram_id}")
    return Reply(text, [[("✅ Одобрить", f"a:L:ok:{item.id}"), ("✏️ Доработать", f"a:L:fix:{item.id}")],
                        [("❌ Отклонить", f"a:L:no:{item.id}")], BACK])


def dating_queue(db: Session, header: str = "") -> Reply:
    profile = db.scalar(select(DatingProfile).where(DatingProfile.status == "pending")
                        .order_by(DatingProfile.id).limit(1))
    if profile is None:
        return Reply(header + "Анкет на проверке нет ✅", [BACK])
    photo = upload_root() / profile.photo_key if profile.photo_key else None
    text = (f"{header}💗 Анкета #{profile.id}\n{age_of(profile.birth_year)} лет · "
            f"{'девушка' if profile.gender == 'f' else 'парень'} · {profile.city}\n\n{_short(profile.bio, 500)}"
            + ("" if photo else "\n\n⚠️ Фото не загружено: опубликовать нельзя"))
    buttons = [("❌ Отклонить", f"a:D:no:{profile.id}")]
    if photo:
        buttons.insert(0, ("✅ Опубликовать", f"a:D:ok:{profile.id}"))
    return Reply(text, [buttons, BACK], photo=photo if photo and photo.exists() else None)


def report_queue(db: Session, header: str = "") -> Reply:
    report = db.scalar(select(Report).where(Report.status == "new").order_by(Report.id).limit(1))
    if report is None:
        return Reply(header + "Новых жалоб нет ✅", [BACK])
    target = f"{report.target_type} {report.target_id}"
    if report.target_type == "listing" and report.target_id.isdigit():
        listing = db.get(Listing, int(report.target_id))
        if listing:
            target = f"объявление #{listing.id}: {_short(listing.title, 80)}\n{_short(listing.description, 300)}"
    count = _count(db, Report, Report.target_type == report.target_type, Report.target_id == report.target_id)
    text = (f"{header}🚩 Жалоба #{report.id}\nНа: {target}\nПричина: {report.reason}\n"
            f"Всего жалоб на этот объект: {count}\n{_short(report.comment, 300)}")
    return Reply(text, [[("✅ Принять (скрыть / заблокировать)", f"a:R:ok:{report.id}")],
                        [("🚫 Отклонить жалобу", f"a:R:no:{report.id}")], BACK])


def employer_queue(db: Session, header: str = "") -> Reply:
    user = db.scalar(select(User).where(User.employer_status == "pending").order_by(User.created_at).limit(1))
    if user is None:
        return Reply(header + "Заявок работодателей нет ✅", [BACK])
    text = (f"{header}🏢 Заявка работодателя\nКомпания: {user.company_name}\n"
            f"Рег. номер: {user.registration_no}\nКонтактное лицо: {user.contact_person}\nID: {user.tg_id}")
    return Reply(text, [[("✅ Подтвердить", f"a:E:ok:{user.tg_id}"), ("❌ Отклонить", f"a:E:no:{user.tg_id}")], BACK])


def user_card(db: Session, tg_id: str) -> Reply:
    user = db.get(User, tg_id)
    if user is None:
        return Reply("Пользователь с таким ID не найден. Отправьте другой ID или /cancel.", [BACK],
                     state={"kind": "find_user"})
    listings = _count(db, Listing, Listing.author_telegram_id == tg_id)
    reports = _count(db, Report, Report.target_type == "user", Report.target_id == tg_id)
    paid = _count(db, Payment, Payment.user_tg_id == tg_id, Payment.status == "paid")
    text = (f"👤 Пользователь {tg_id}\nГород: {user.city}\nСогласие: {'да' if user.consent_at else 'нет'}\n"
            f"Объявлений: {listings} · жалоб на него: {reports} · оплат: {paid}\n"
            f"Статус: {'🚫 заблокирован — ' + (user.block_reason or 'без причины') if user.blocked else 'активен'}"
            + ("\nАккаунт удалён" if user.deleted_at else ""))
    action = ("✅ Разблокировать", f"a:U:unb:{tg_id}") if user.blocked else ("🚫 Заблокировать", f"a:U:blk:{tg_id}")
    return Reply(text, [[action], BACK])


def stats_view(db: Session) -> Reply:
    s = ops.compute_stats(db)
    by_status = ", ".join(f"{k}: {v}" for k, v in sorted(s["listings"].items())) or "нет"
    top = "\n".join(f"  {code} — {n}" for code, n in s["top_services"][:5]) or "  пока нет продаж"
    text = (f"📊 Статистика\nПользователей: {s['users']}\nНовых жалоб: {s['reports_new']}\n"
            f"Платящих: {s['payers']}\nВыручка всего: {s['revenue_stars_total']} ⭐\n"
            f"За сутки: {s['day']['stars']} ⭐ ({s['day']['sales']} продаж)\n"
            f"За неделю: {s['week']['stars']} ⭐ ({s['week']['sales']})\n"
            f"За месяц: {s['month']['stars']} ⭐ ({s['month']['sales']})\n"
            f"Объявления: {by_status}\nПопулярные услуги:\n{top}")
    return Reply(text, [BACK])


def payments_view(db: Session) -> Reply:
    rows = db.scalars(select(Payment).order_by(desc(Payment.id)).limit(8)).all()
    if not rows:
        return Reply("💳 Платежей пока нет.", [BACK])
    lines = [f"#{p.id} · польз. {p.user_tg_id} · {p.service_code} · {p.stars} ⭐ · {p.status}" for p in rows]
    buttons = [[(f"↩️ Вернуть #{p.id}", f"a:P:rf:{p.id}")] for p in rows if p.status == "paid"]
    return Reply("💳 Последние платежи:\n" + "\n".join(lines), buttons + [BACK])


def prices_view(db: Session) -> Reply:
    payments.ensure_pricing(db)
    rows = db.scalars(select(Pricing).order_by(Pricing.code)).all()
    lines = [f"{'🟢' if p.is_active else '⚪️'} {p.code}: {p.stars} ⭐ ({p.title})" for p in rows]
    buttons = [[(f"✏️ {p.code}", f"a:C:p:{p.code}"),
                ("⏸ Выключить" if p.is_active else "▶️ Включить", f"a:C:t:{p.code}")] for p in rows]
    return Reply("💰 Цены услуг\n" + "\n".join(lines), buttons + [BACK])


def audit_view(db: Session) -> Reply:
    rows = db.scalars(select(AuditLog).order_by(desc(AuditLog.id)).limit(15)).all()
    lines = [f"{str(r.created_at)[5:16]} · {r.actor} · {r.action} · {r.object_type} {r.object_id}" for r in rows]
    return Reply("📜 Журнал действий (последние 15):\n" + ("\n".join(lines) or "пока пуст"), [BACK])


def staff_view(db: Session) -> Reply:
    rows = db.scalars(select(Admin).order_by(Admin.created_at)).all()
    lines = [f"{a.tg_id} · {a.role}{'' if a.active else ' (отключён)'}" for a in rows]
    return Reply("🧑‍💼 Сотрудники:\n" + "\n".join(lines) +
                 "\n\nДобавить или изменить: нажмите кнопку и отправьте «ID роль», например\n"
                 "123456789 moderator\nОтключить: «ID off».",
                 [[("➕ Добавить / изменить", "a:T:add")], BACK])


# ---------- button presses ----------

def _handle(db: Session, admin_id: int, role: str, parts: list[str]) -> Reply:
    """parts is the callback data split by ':' — for example ['a', 'L', 'ok', '12']."""
    actor = str(admin_id)
    section = parts[1] if len(parts) > 1 else "menu"
    if section == "menu":
        PENDING_INPUT.pop(admin_id, None)
        return menu(db, role)
    if role not in LEVEL.get(section, MOD):
        return Reply("", alert="Недостаточно прав для этого раздела")
    action = parts[2] if len(parts) > 2 else ""
    arg = parts[3] if len(parts) > 3 else ""

    try:
        if section == "L":
            if action in ("fix", "no"):
                return Reply(("Напишите, что исправить (автор это увидит)" if action == "fix"
                              else "Напишите причину отказа (автор её увидит)") + ", или /cancel:",
                             state={"kind": f"listing_{action}", "id": int(arg)})
            if action == "ok":
                item = ops.moderate_listing(db, actor, int(arg), "approve")
                reply = listing_queue(db, f"✅ #{item.id} опубликовано\n\n")
                reply.notify.append((item.author_telegram_id, f"Ваше объявление «{_short(item.title, 60)}» одобрено и опубликовано."))
                return reply
            return listing_queue(db)
        if section == "D":
            if action in ("ok", "no"):
                profile = ops.decide_dating(db, actor, int(arg), "approve" if action == "ok" else "reject")
                reply = dating_queue(db, f"{'✅ Опубликована' if action == 'ok' else '❌ Отклонена'} анкета #{profile.id}\n\n")
                word = "опубликована" if action == "ok" else "отклонена"
                reply.notify.append((profile.owner_tg_id, f"Ваша анкета знакомств {word}."))
                return reply
            return dating_queue(db)
        if section == "R":
            if action in ("ok", "no"):
                report = ops.decide_report(db, actor, int(arg), "resolve" if action == "ok" else "reject")
                return report_queue(db, f"{'✅ Принята' if action == 'ok' else '🚫 Отклонена'} жалоба #{report.id}\n\n")
            return report_queue(db)
        if section == "E":
            if action in ("ok", "no"):
                user = ops.decide_employer(db, actor, arg, "approve" if action == "ok" else "reject")
                reply = employer_queue(db, f"{'✅ Подтверждён' if action == 'ok' else '❌ Отклонён'}: {user.company_name}\n\n")
                reply.notify.append((arg, "Статус работодателя подтверждён." if action == "ok"
                                     else "Заявка работодателя отклонена."))
                return reply
            return employer_queue(db)
        if section == "U":
            if action in ("blk", "unb"):
                return Reply(("Напишите причину блокировки" if action == "blk" else "Напишите причину разблокировки")
                             + " (не короче 3 символов) или /cancel:",
                             state={"kind": "block" if action == "blk" else "unblock", "id": arg})
            return Reply("Отправьте Telegram ID пользователя (число) или /cancel:", state={"kind": "find_user"})
        if section == "S":
            return stats_view(db)
        if section == "P":
            if action == "rf":
                return Reply(f"Вернуть оплату #{arg}? Звёзды вернутся пользователю, услуга отключится.",
                             [[("✅ Да, вернуть", f"a:P:rfy:{arg}"), ("Отмена", "a:P")]])
            if action == "rfy":
                payment = payments.refund(db, int(arg))
                log_action(db, actor, "payment.refund", "payment", payment.id, payment.telegram_payment_charge_id or "")
                db.commit()
                reply = payments_view(db)
                reply.text = f"✅ Оплата #{payment.id} возвращена\n\n" + reply.text
                reply.notify.append((payment.user_tg_id, f"Оплата #{payment.id} возвращена, услуга отключена."))
                return reply
            return payments_view(db)
        if section == "C":
            if action == "p":
                return Reply(f"Новая цена для {arg} в звёздах (число от 1 до 10000) или /cancel:",
                             state={"kind": "price", "code": arg})
            if action == "t":
                pricing = db.get(Pricing, arg)
                if pricing is None:
                    return Reply("", alert="Услуга не найдена")
                payments.set_price(db, arg, None, not pricing.is_active)
                log_action(db, actor, "pricing.update", "pricing", arg, f"active={not pricing.is_active}")
                db.commit()
                return prices_view(db)
            return prices_view(db)
        if section == "J":
            return audit_view(db)
        if section == "T":
            if action == "add":
                return Reply("Отправьте «ID роль», например 123456789 moderator (роли: moderator, admin, super_admin) "
                             "или «ID off», чтобы отключить. /cancel — отмена:", state={"kind": "add_staff"})
            return staff_view(db)
    except (ops.OpError, payments.PaymentError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        return Reply("", alert=_short(message, 180))
    except ValueError:
        return Reply("", alert="Некорректные данные кнопки")
    return menu(db, role)


# ---------- text answers ----------

def _handle_text(db: Session, admin_id: int, role: str, text: str) -> Reply | None:
    """Consume the administrator's answer to a question the bot asked. None = not waiting for anything."""
    state = PENDING_INPUT.get(admin_id)
    if state is None:
        return None
    if role not in INPUT_LEVEL.get(state["kind"], SUP):
        PENDING_INPUT.pop(admin_id, None)
        return Reply("Недостаточно прав для этого действия.", [BACK])
    actor = str(admin_id)
    text = text.strip()
    kind = state["kind"]
    try:
        if kind in ("listing_fix", "listing_no"):
            item = ops.moderate_listing(db, actor, state["id"], "needs_fix" if kind == "listing_fix" else "reject", text)
            PENDING_INPUT.pop(admin_id, None)
            word = "нужно исправить" if kind == "listing_fix" else "отклонено"
            reply = listing_queue(db, f"{'✏️ Возвращено на доработку' if kind == 'listing_fix' else '❌ Отклонено'}: #{item.id}\n\n")
            reply.notify.append((item.author_telegram_id,
                                 f"Объявление «{_short(item.title, 60)}»: {word}.\nКомментарий: {item.note}"))
            return reply
        if kind == "find_user":
            if not text.isdigit():
                return Reply("ID — это число. Отправьте ID ещё раз или /cancel.", state=state)
            PENDING_INPUT.pop(admin_id, None)
            return user_card(db, text)
        if kind in ("block", "unblock"):
            user = (ops.block_user if kind == "block" else ops.unblock_user)(db, actor, state["id"], text)
            PENDING_INPUT.pop(admin_id, None)
            reply = user_card(db, user.tg_id)
            reply.text = ("🚫 Пользователь заблокирован\n\n" if kind == "block" else "✅ Пользователь разблокирован\n\n") + reply.text
            return reply
        if kind == "price":
            if not text.isdigit():
                return Reply("Нужно целое число звёзд. Отправьте число ещё раз или /cancel.", state=state)
            payments.set_price(db, state["code"], int(text), None)
            log_action(db, actor, "pricing.update", "pricing", state["code"], f"stars={text}")
            db.commit()
            PENDING_INPUT.pop(admin_id, None)
            reply = prices_view(db)
            reply.text = "✅ Цена сохранена\n\n" + reply.text
            return reply
        if kind == "add_staff":
            parts = text.split()
            if len(parts) != 2 or not parts[0].isdigit() or (parts[1] not in ROLES and parts[1] != "off"):
                return Reply("Формат: «ID роль» (moderator, admin, super_admin) или «ID off». Попробуйте ещё раз или /cancel.", state=state)
            tg, role_name = parts
            if tg == actor and role_name != "super_admin":
                return Reply("Нельзя лишить доступа самого себя.", state=state)
            staff = db.get(Admin, tg)
            if staff is None:
                if role_name == "off":
                    return Reply("Такого сотрудника нет.", state=state)
                staff = Admin(tg_id=tg, role=role_name, active=True, created_at=payments.now())
                db.add(staff)
            elif role_name == "off":
                staff.active = False
            else:
                staff.role, staff.active = role_name, True
            log_action(db, actor, "admin.set", "admin", tg, f"{staff.role} active={staff.active}")
            db.commit()
            PENDING_INPUT.pop(admin_id, None)
            return staff_view(db)
    except (ops.OpError, payments.PaymentError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        return Reply(f"Не получилось: {message}\nПопробуйте ещё раз или /cancel.", state=state)
    return None


def handle(db: Session, admin_id: int, role: str, parts: list[str]) -> Reply:
    reply = _handle(db, admin_id, role, parts)
    if reply.state is not None:
        PENDING_INPUT[admin_id] = reply.state
    return reply


def handle_text(db: Session, admin_id: int, role: str, text: str) -> Reply | None:
    reply = _handle_text(db, admin_id, role, text)
    if reply is not None and reply.state is not None:
        PENDING_INPUT[admin_id] = reply.state
    return reply


# ---------- Telegram layer: only sends what the functions above return ----------

def _markup(rows):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
                                                 for row in rows])


async def _send(bot, chat_id: int, reply: Reply) -> None:
    from aiogram.types import FSInputFile
    markup = _markup(reply.rows)
    sent = False
    if reply.photo is not None:
        try:
            await bot.send_photo(chat_id, FSInputFile(reply.photo), caption=reply.text[:1000], reply_markup=markup)
            sent = True
        except Exception:
            sent = False
    if not sent:
        await bot.send_message(chat_id, reply.text[:4000], reply_markup=markup)
    for tg_id, text in reply.notify:
        try:
            await bot.send_message(int(tg_id), text)
        except Exception:
            pass  # the person may have blocked the bot; the decision is already saved


def register_admin(dp, bot) -> None:
    from aiogram import F
    from aiogram.filters import Command
    from aiogram.types import CallbackQuery, Message
    from app.database import SessionLocal

    @dp.message(Command("admin"))
    async def open_admin(message: Message):
        with SessionLocal() as db:
            role = role_of(db, message.from_user.id)
            reply = None if role is None else handle(db, message.from_user.id, role, ["a", "menu"])
        if reply is None:
            await message.answer("Команда доступна только сотрудникам.")
            return
        await _send(bot, message.chat.id, reply)

    @dp.message(Command("cancel"))
    async def cancel(message: Message):
        had = PENDING_INPUT.pop(message.from_user.id, None) is not None
        await message.answer("Отменено. /admin — меню." if had else "Нечего отменять.")

    @dp.callback_query(F.data.startswith("a:"))
    async def pressed(query: CallbackQuery):
        with SessionLocal() as db:
            role = role_of(db, query.from_user.id)
            reply = (Reply("", alert="Нет доступа") if role is None
                     else handle(db, query.from_user.id, role, query.data.split(":")))
        if reply.alert:
            await query.answer(reply.alert, show_alert=True)
            return
        try:
            await query.message.edit_reply_markup(reply_markup=None)  # старые кнопки больше не нужны
        except Exception:
            pass
        await query.answer()
        await _send(bot, query.message.chat.id, reply)

    @dp.message(F.text & ~F.text.startswith("/"))
    async def answered(message: Message):
        with SessionLocal() as db:
            role = role_of(db, message.from_user.id)
            reply = None if role is None else handle_text(db, message.from_user.id, role, message.text)
        if reply is not None:
            await _send(bot, message.chat.id, reply)
