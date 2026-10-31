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
from app import adminx, lifecycle, payments
from app.fields import LABELS
from app.photos import photo_root
from app.admin_auth import MOD, ADM, SUP, SUPPORT, ROLES
from app.audit import log_action
from app.dating import age_of, upload_root
from app.models import (Region, Article, Banner, Broadcast, Group, PromoCode, Admin, AuditLog, DatingProfile, Listing, ListingPhoto, Payment, Pricing, Report, User)

CATEGORY = {"job": "Работа", "housing": "Жильё", "marketplace": "Барахолка"}
BACK = [("⬅️ В меню", "a:menu")]
# What each section needs. The menu is open to every staff member.
LEVEL = {"L": MOD, "D": MOD, "R": SUPPORT, "E": MOD, "U": SUPPORT, "N": ADM, "B": ADM, "G": ADM, "W": ADM, "S": ADM, "P": ADM, "C": ADM, "K": ADM, "J": ADM, "T": SUP}
# A text answer the bot is waiting for from an administrator: {admin_id: {"kind": ..., ...}}
PENDING_INPUT: dict[int, dict] = {}
INPUT_LEVEL = {"listing_hide": MOD, "find_listing": MOD, "listing_fix": MOD, "listing_no": MOD, "find_user": SUPPORT, "block": SUPPORT, "unblock": SUPPORT,
               "bc_text": ADM, "banner_new": ADM, "group_link": ADM, "group_members": ADM, "article_src": ADM,
               "price": ADM, "promo_new": ADM, "add_staff": SUP}


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
    if role == "support":
        rows = [[(f"🚩 Жалобы ({_count(db, Report, Report.status == 'new')})", "a:R"), ("👥 Пользователи", "a:U")]]
        return Reply("🛠 Поддержка KOREA HUB\nВам доступны жалобы, пользователи и блокировки.", rows)
    rows = [
        [(f"📋 Объявления ({_count(db, Listing, ops.queue_condition())})", "a:L"),
         (f"💗 Анкеты ({_count(db, DatingProfile, DatingProfile.status == 'pending')})", "a:D")],
        [(f"🚩 Жалобы ({_count(db, Report, Report.status == 'new')})", "a:R"),
         (f"🏢 Работодатели ({_count(db, User, User.employer_status == 'pending')})", "a:E")],
        [("👥 Пользователи", "a:U"), ("🔎 Найти объявление", "a:L:find")],
    ]
    if role in ADM:
        rows += [[("📊 Статистика", "a:S"), ("📈 За 30 дней", "a:N")], [("💳 Платежи", "a:P"), ("💰 Цены", "a:C")],
                 [("🎟 Промокоды", "a:K"), ("📣 Рассылка", "a:B")], [("🖼 Баннеры", "a:W"), ("📚 Группы и гид", "a:G")],
                 [("📜 Журнал", "a:J")]]
    if role in SUP:
        rows.append([("🧑‍💼 Сотрудники", "a:T")])
    return Reply("🛠 Админ-панель KOREA HUB\nВаша роль: " + role + "\nВыберите раздел:", rows)


FIELD_LABELS = {"title": "Название", "description": "Описание", "contact": "Контакт", "price": "Пометка к цене",
                "price_amount": "Сумма", "city": "Место", "region_code": "Регион", "district_id": "Район",
                "details": "Подробности"}


def _facts(item: Listing) -> str:
    """Key facts a moderator needs: pay or price, visas, schedule, company…"""
    d = item.details or {}
    bits = []
    if item.price_amount is not None:
        bits.append(f"{item.price_amount:,}".replace(",", " ") + " ₩")
    if item.category == "job":
        bits += [x for x in (d.get("company"), d.get("position"), item.schedule, item.employment) if x]
        if item.visas:
            bits.append("визы " + item.visas.strip(","))
        bits += [lbl for ok, lbl in ((item.housing_provided, "жильё"), (item.meals_provided, "питание")) if ok]
    elif item.category == "housing":
        bits += [str(x) for x in (item.housing_type, f"залог {item.deposit:,}".replace(",", " ") if item.deposit else None,
                                  f"{item.area_m2} м²" if item.area_m2 else None, d.get("station")) if x]
    else:
        goods = {k: v for k, v in LABELS["goods_category"]}.get(item.goods_category or "", item.goods_category)
        cond = {k: v for k, v in LABELS["condition"]}.get(item.condition or "", item.condition)
        bits += [str(x) for x in (goods, cond, d.get("brand"), d.get("model"),
                                  f"{d['year']} г." if d.get("year") else None,
                                  f"{d['mileage_km']:,} км".replace(",", " ") if d.get("mileage_km") is not None else None,
                                  f"выезд: {d['service_area']}" if d.get("service_area") else None,
                                  f"опыт {d['experience_years']} лет" if d.get("experience_years") is not None else None) if x]
    return " · ".join(bits) or "—"


def _edit_lines(item: Listing, db: Session) -> str:
    lines = []
    for key, new in (item.pending_changes or {}).items():
        old = getattr(item, key, None)
        lines.append(f"• {FIELD_LABELS.get(key, key)}: {_short(str(old), 60)} → {_short(str(new), 120)}")
    waiting = db.scalars(select(ListingPhoto).where(ListingPhoto.listing_id == item.id,
                                                    ListingPhoto.status.in_(["pending", "removal"]))).all()
    added = sum(1 for p in waiting if p.status == "pending")
    removed = len(waiting) - added
    if added or removed:
        lines.append(f"• Фото: +{added} / −{removed}")
    return "\n".join(lines) or "• без изменений текста"


def _listing_photo(item: Listing, db: Session) -> Path | None:
    """The first photo a moderator should look at (waiting ones first on an edit)."""
    rows = db.scalars(select(ListingPhoto).where(ListingPhoto.listing_id == item.id,
                                                 ListingPhoto.status.in_(["pending", "live"]))
                      .order_by(ListingPhoto.status.desc(), ListingPhoto.position)).all()
    for p in rows:
        path = photo_root() / f"{p.key}.jpg"
        if path.exists():
            return path
    return None


STATUS_WORD = {"draft": "черновик", "pending": "на проверке", "approved": "опубликовано", "rejected": "отклонено",
               "needs_fix": "на доработке", "hidden": "скрыто", "closed": "закрыто автором", "sold": "продано",
               "expired": "срок истёк", "archived": "удалено автором"}


def listing_card(db: Session, listing_id: int, header: str = "") -> Reply:
    """Any listing by its number, with the actions that fit its state."""
    item = db.get(Listing, listing_id)
    if item is None:
        return Reply(header + f"Объявление #{listing_id} не найдено.", [[("🔎 Искать ещё", "a:L:find")], BACK])
    photos = db.scalar(select(func.count()).select_from(ListingPhoto).where(ListingPhoto.listing_id == item.id)) or 0
    text = (f"{header}Объявление #{item.id}: {STATUS_WORD.get(item.status, item.status)}"
            f"{' · есть правка на проверке' if item.has_edit else ''}\n"
            f"{CATEGORY.get(item.category, item.category)} · {item.city}\n{_facts(item)}\n\n"
            f"{_short(item.title, 160)}\n{_short(item.description, 600)}\n\n"
            f"Фото: {photos} · просмотров: {item.views or 0}\nКонтакт: {item.contact or '—'}\nАвтор: {item.author_telegram_id}"
            + (f"\nКомментарий: {item.note}" if item.note else ""))
    rows = []
    if item.status == "approved":
        rows.append([("🙈 Скрыть", f"a:L:hide:{item.id}")])
    if item.status == "hidden":
        rows.append([("👁 Показать", f"a:L:show:{item.id}")])
    if item.status == "pending" or item.has_edit:
        rows.append([("📋 К очереди", "a:L")])
    rows += [[("🔎 Искать ещё", "a:L:find")], BACK]
    return Reply(text, rows, photo=_listing_photo(item, db))


def _listing_decision(db: Session, actor: str, listing_id: int, decision: str, reason: str) -> Reply:
    """Return for fixes or reject (an edit of a published listing only drops the edit), tell the author, show the next one."""
    before = db.get(Listing, listing_id)
    was_edit = bool(before and before.status == "approved" and before.has_edit)
    item = ops.moderate_listing(db, actor, listing_id, decision, reason)
    word = ("правка отклонена, объявление осталось опубликованным" if was_edit
            else "нужно исправить" if decision == "needs_fix" else "отклонено")
    reply = listing_queue(db, f"{'✏️ Возвращено на доработку' if decision == 'needs_fix' else '❌ Отклонено'}: #{item.id}\n\n")
    reply.notify.append((item.author_telegram_id, f"Объявление «{_short(item.title, 60)}»: {word}.\nКомментарий: {item.note}"))
    return reply


def listing_queue(db: Session, header: str = "") -> Reply:
    item = db.scalar(select(Listing).where(ops.queue_condition()).order_by(Listing.id).limit(1))
    waiting = _count(db, Listing, ops.queue_condition())
    if item is None:
        return Reply(header + "Очередь объявлений пуста ✅", [BACK])
    edit = item.status == "approved" and item.has_edit
    no_photo = (not edit) and lifecycle.needs_photo(db, item)
    title = "✏️ Правка опубликованного" if edit else "📋 Объявление"
    body = (f"Что меняется:\n{_edit_lines(item, db)}" if edit else
            f"Заголовок: {_short(item.title, 160)}\n\n{_short(item.description, 800)}")
    text = (f"{header}{title} #{item.id} (в очереди: {waiting})\n"
            f"{CATEGORY.get(item.category, item.category)} · {item.city}\n{_facts(item)}\n\n{body}\n\n"
            f"Контакт: {item.contact or '—'}\nАвтор: {item.author_telegram_id}"
            + ("".join(f"\n⚠️ {f}" for f in adminx.listing_flags(db, item)))
            + ("\n\n⚠️ Нет фото: для этого раздела они обязательны, одобрить нельзя" if no_photo else ""))
    first = [] if no_photo else [("✅ " + ("Одобрить правку" if edit else "Одобрить"), f"a:L:ok:{item.id}")]
    return Reply(text, [first + [("✏️ Доработать", f"a:L:fix:{item.id}")], [("❌ Отклонить", f"a:L:no:{item.id}")], BACK],
                 photo=_listing_photo(item, db))


def dating_queue(db: Session, header: str = "") -> Reply:
    profile = db.scalar(select(DatingProfile).where(DatingProfile.status == "pending")
                        .order_by(DatingProfile.id).limit(1))
    if profile is None:
        return Reply(header + "Анкет на проверке нет ✅", [BACK])
    photo = upload_root() / profile.photo_key if profile.photo_key else None
    text = (f"{header}💗 Анкета #{profile.id}" + (f" · {profile.first_name}" if profile.first_name else "") + f"\n{age_of(profile.birth_year)} лет · "
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
    act = adminx.user_activity(db, tg_id)
    searches = "; ".join(f"{s['name']}" for s in act["saved_searches"][:3]) or "нет"
    text += (f"\nОтклики: {act['applications_total']}" + (f" ({', '.join(f'{k}: {v}' for k, v in act['applications'].items())})" if act["applications"] else "")
             + f"\nПоиски под наблюдением: {len(act['saved_searches'])} ({searches})\nЖалоб подано: {act['reports_made']}")
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


def promos_view(db: Session, header: str = "") -> Reply:
    rows = db.scalars(select(PromoCode).order_by(PromoCode.id.desc()).limit(10)).all()
    lines = []
    for p in rows:
        off = f"−{p.percent}%" if p.percent else f"−{p.fixed_stars} ⭐"
        lines.append(f"{'🟢' if p.is_active else '⚪️'} {p.code}: {off} · использовано {p.used_count}"
                     + (f"/{p.max_uses}" if p.max_uses else "") + (f" · до {p.valid_until:%d.%m.%Y}" if p.valid_until else ""))
    buttons = [[(("⏸ " if p.is_active else "▶️ ") + p.code, f"a:K:t:{p.id}")] for p in rows]
    return Reply(header + "🎟 Промокоды\n" + ("\n".join(lines) if lines else "Пока нет промокодов."),
                 [[("➕ Создать", "a:K:new")]] + buttons + [BACK])


def analytics_view(db: Session) -> Reply:
    data = adminx.analytics(db, 30)
    week = data["days"][-7:]
    f = data["funnel"]
    text = ("📈 За 30 дней\n"
            f"Новых пользователей: {data['totals']['users']} (за 7 дней: {sum(d['users'] for d in week)})\n"
            f"Новых объявлений: {data['totals']['listings']} (за 7 дней: {sum(d['listings'] for d in week)})\n"
            f"Откликов: {data['totals']['applications']}\nЗаработано: {data['totals']['stars']} ⭐ (за 7 дней: {sum(d['stars'] for d in week)})\n\n"
            f"Воронка за всё время: просмотры {f['views']} → чаты {f['chats']} → отклики {f['applications']} → оплаты {f['paid']}\n"
            + ("\nПо услугам:\n" + "\n".join(f"• {s['service']}: {s['stars']} ⭐" for s in data["stars_by_service"][:5]) if data["stars_by_service"] else ""))
    return Reply(text, [BACK])


def broadcast_preview(db: Session, b: Broadcast, header: str = "") -> Reply:
    region = db.get(Region, b.region_code).name_ru if b.region_code else "вся Корея"
    text = f"{header}📣 Рассылка #{b.id}\nКому: {region} — получателей: {b.total}\n\n{b.text}"
    if b.status == "draft":
        return Reply(text + "\n\nОтправить?", [[("📤 Отправить", f"a:B:go:{b.id}"), ("❌ Отмена", f"a:B:no:{b.id}")],
                                              [("📍 Только один регион", f"a:B:rg:{b.id}")], BACK])
    return Reply(f"{text}\n\nСтатус: {b.status} · отправлено {b.sent}, ошибок {b.failed} из {b.total}",
                 ([[("⏹ Остановить", f"a:B:no:{b.id}")]] if b.status == "sending" else []) + [[("🔄 Обновить", f"a:B:v:{b.id}")], BACK])


def broadcasts_view(db: Session, header: str = "") -> Reply:
    rows = db.scalars(select(Broadcast).order_by(Broadcast.id.desc()).limit(6)).all()
    lines = [f"#{b.id} · {b.status} · {b.sent}/{b.total} · {_short(b.text, 40)}" for b in rows]
    return Reply(header + "📣 Рассылки\n" + ("\n".join(lines) if lines else "Пока не было."),
                 [[("➕ Новая рассылка", "a:B:new")]] + [[(f"#{b.id} {b.status}", f"a:B:v:{b.id}")] for b in rows] + [BACK])


def banners_view(db: Session, header: str = "") -> Reply:
    rows = db.scalars(select(Banner).order_by(Banner.id.desc()).limit(8)).all()
    lines = [f"{'🟢' if b.is_active else '⚪️'} #{b.id} {_short(b.text, 50)}" + (f" → {b.link}" if b.link else "") for b in rows]
    buttons = [[(("⏸ " if b.is_active else "▶️ ") + f"#{b.id}", f"a:W:t:{b.id}"), (f"🗑 #{b.id}", f"a:W:d:{b.id}")] for b in rows]
    return Reply(header + "🖼 Баннеры на главной (показываются до трёх)\n" + ("\n".join(lines) if lines else "Пока нет."),
                 [[("➕ Новый баннер", "a:W:new")]] + buttons + [BACK])


def content_view(db: Session, header: str = "") -> Reply:
    groups = db.scalars(select(Group).order_by(Group.id)).all()
    arts = db.scalars(select(Article).order_by(Article.section, Article.id)).all()
    glines = [f"{g.flag} {g.name}: {'🔗' if g.telegram_link else 'нет ссылки'} · {g.members_count} уч." for g in groups[:12]]
    alines = [f"{'🟢' if a.published else '⚪️'} #{a.id} {_short(a.title, 36)}" + ("" if a.source_url else " (нет источника)") for a in arts[:12]]
    rows = [[(f"🔗 {g.name[:18]}", f"a:G:l:{g.id}"), (f"👥 {g.id}", f"a:G:m:{g.id}")] for g in groups[:12]]
    rows += [[(("⏸ " if a.published else "✅ ") + f"#{a.id}", f"a:G:p:{a.id}"), (f"📎 источник #{a.id}", f"a:G:s:{a.id}")] for a in arts[:12]]
    return Reply(header + "📚 Группы сообщества\n" + "\n".join(glines) + "\n\n📘 Статьи гида (текст пишется в веб-админке)\n" + "\n".join(alines),
                 rows + [BACK])


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
            if action in ("fix", "no"):   # first the ready-made reasons, then "write your own"
                rows = [[(t[:46], f"a:L:{action}t:{arg}:{i}")] for i, (_k, t) in enumerate(adminx.REASON_TEMPLATES)]
                rows.append([("✍️ Написать свою причину", f"a:L:{action}x:{arg}")])
                rows.append([("↩ К очереди", "a:L")])
                return Reply("Выберите причину (автор её увидит):" if action == "no" else "Что нужно исправить (автор это увидит)?", rows)
            if action in ("fixx", "nox"):
                return Reply(("Напишите, что исправить (автор это увидит)" if action == "fixx"
                              else "Напишите причину отказа (автор её увидит)") + ", или /cancel:",
                             state={"kind": "listing_fix" if action == "fixx" else "listing_no", "id": int(arg)})
            if action in ("fixt", "not"):
                idx = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else -1
                if not 0 <= idx < len(adminx.REASON_TEMPLATES):
                    return Reply("", alert="Шаблон не найден")
                return _listing_decision(db, actor, int(arg), "needs_fix" if action == "fixt" else "reject",
                                         adminx.REASON_TEMPLATES[idx][1])
            if action == "find":
                return Reply("Отправьте номер объявления (число) или /cancel:", state={"kind": "find_listing"})
            if action in ("hide", "show"):
                if action == "hide":
                    return Reply("Напишите причину, почему скрыть (автор её увидит), или /cancel:",
                                 state={"kind": "listing_hide", "id": int(arg)})
                item = ops.moderate_listing(db, actor, int(arg), "show")
                reply = listing_card(db, item.id, "👁 Объявление снова опубликовано\n\n")
                reply.notify.append((item.author_telegram_id, f"Объявление «{_short(item.title, 60)}» снова опубликовано."))
                return reply
            if action == "ok":
                before = db.get(Listing, int(arg))
                was_edit = bool(before and before.status == "approved" and before.has_edit)
                item = ops.moderate_listing(db, actor, int(arg), "approve")
                reply = listing_queue(db, f"✅ #{item.id} {'правка опубликована' if was_edit else 'опубликовано'}\n\n")
                reply.notify.append((item.author_telegram_id, f"Ваше объявление «{_short(item.title, 60)}»: " +
                                     ("изменения одобрены и опубликованы." if was_edit else "одобрено и опубликовано.")))
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
        if section == "K":
            if action == "new":
                return Reply("Напишите промокод одной строкой:\nКОД 20% [лимит] [дней]  — скидка в процентах\n"
                             "КОД 30⭐ [лимит] [дней]  — скидка в звёздах\nНапример: SPRING 20% 50 30\nИли /cancel:",
                             state={"kind": "promo_new"})
            if action == "t":
                promo = db.get(PromoCode, int(arg))
                if promo is None:
                    return Reply("", alert="Промокод не найден")
                promo.is_active = not promo.is_active
                log_action(db, actor, "promo.toggle", "promo", promo.code, f"active={promo.is_active}")
                db.commit()
            return promos_view(db)
        if section == "N":
            return analytics_view(db)
        if section == "B":
            if action == "new":
                return Reply("Напишите текст рассылки (до 500 символов) или /cancel.\nСначала я покажу, сколько человек его получит.",
                             state={"kind": "bc_text"})
            if action == "v":
                b = db.get(Broadcast, int(arg))
                return broadcast_preview(db, b) if b else Reply("", alert="Рассылка не найдена")
            if action == "rg":
                regs = db.scalars(select(Region).order_by(Region.sort)).all()
                rows = [[(r.name_ru, f"a:B:rc:{arg}:{r.code}") for r in regs[i:i + 3]] for i in range(0, len(regs), 3)]
                rows.append([("🇰🇷 Вся Корея", f"a:B:rc:{arg}:-")])
                return Reply("Кому отправить?", rows)
            if action == "rc":
                b = db.get(Broadcast, int(arg))
                if b is None or b.status != "draft":
                    return Reply("", alert="Рассылка уже запущена")
                code = parts[4] if len(parts) > 4 and parts[4] != "-" else None
                b.region_code = code
                b.total = len(adminx.audience(db, code))
                db.commit()
                return broadcast_preview(db, b)
            if action in ("go", "no"):
                try:
                    b = (adminx.start_broadcast if action == "go" else adminx.cancel_broadcast)(db, actor, int(arg))
                except Exception as e:  # HTTPException from the shared function
                    return Reply("", alert=str(getattr(e, "detail", e)))
                return broadcast_preview(db, b, "🚀 Рассылка запущена, отправка идёт постепенно\n\n" if action == "go" else "⏹ Остановлено\n\n")
            return broadcasts_view(db)
        if section == "W":
            if action == "new":
                return Reply("Напишите баннер одной строкой:\nТекст | ссылка (необязательно) | дней показа (необязательно)\n"
                             "Например: Новые вакансии в Пусане | https://t.me/koreahub | 7\nИли /cancel:", state={"kind": "banner_new"})
            if action in ("t", "d"):
                banner = db.get(Banner, int(arg))
                if banner is None:
                    return Reply("", alert="Баннер не найден")
                if action == "t":
                    banner.is_active = not banner.is_active
                    log_action(db, actor, "banner.toggle", "banner", banner.id, f"active={banner.is_active}")
                else:
                    log_action(db, actor, "banner.delete", "banner", banner.id, banner.text[:40])
                    db.delete(banner)
                db.commit()
            return banners_view(db)
        if section == "G":
            if action == "l":
                return Reply("Отправьте ссылку на группу (https://t.me/…) или /cancel:", state={"kind": "group_link", "id": int(arg)})
            if action == "m":
                return Reply("Сколько участников в группе? Число или /cancel:", state={"kind": "group_members", "id": int(arg)})
            if action == "s":
                return Reply("Отправьте ссылку на официальный источник статьи (https://…) или /cancel.\nДата проверки поставится сегодняшняя.",
                             state={"kind": "article_src", "id": int(arg)})
            if action == "p":
                art = db.get(Article, int(arg))
                if art is None:
                    return Reply("", alert="Статья не найдена")
                if not art.published and (not art.source_url.startswith("http") or art.checked_at is None):
                    return Reply("", alert="Сначала укажите источник (кнопка «источник»)")
                if not art.published and not art.body.strip():
                    return Reply("", alert="У статьи нет текста: допишите его в веб-админке")
                art.published = not art.published
                log_action(db, actor, "article.update", "article", art.id, f"published={art.published}")
                db.commit()
            return content_view(db)
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
            PENDING_INPUT.pop(admin_id, None)   # (re-asked below if the reason is refused)
            try:
                return _listing_decision(db, actor, state["id"], "needs_fix" if kind == "listing_fix" else "reject", text)
            except ops.OpError:
                PENDING_INPUT[admin_id] = state
                raise
        if kind == "promo_new":
            from app import promos
            from fastapi import HTTPException
            parts = text.replace("⭐", "").split()
            try:
                if len(parts) < 2:
                    raise ValueError
                value = parts[1]
                percent = int(value[:-1]) if value.endswith("%") else None
                stars = None if percent is not None else int(value)
                payload = promos.PromoIn(code=parts[0], percent=percent, fixed_stars=stars,
                                         max_uses=int(parts[2]) if len(parts) > 2 else None,
                                         days=int(parts[3]) if len(parts) > 3 else None)
                promo = promos.create_promo(db, actor, payload)
            except HTTPException as e:
                return Reply(f"{e.detail}. Исправьте и отправьте ещё раз или /cancel.", state=state)
            except ValueError:
                return Reply("Не разобрал. Формат: КОД 20% [лимит] [дней] или КОД 30⭐ [лимит] [дней]. Ещё раз или /cancel.", state=state)
            PENDING_INPUT.pop(admin_id, None)
            return promos_view(db, f"✅ Создан промокод {promo.code}\n\n")
        if kind == "bc_text":
            if len(text) < 3 or len(text) > 500:
                return Reply("Текст должен быть от 3 до 500 символов. Ещё раз или /cancel.", state=state)
            PENDING_INPUT.pop(admin_id, None)
            return broadcast_preview(db, adminx.create_broadcast(db, actor, text, None))
        if kind == "banner_new":
            from fastapi import HTTPException
            bits = [x.strip() for x in text.split("|")]
            try:
                banner = adminx.create_banner(db, actor, adminx.BannerIn(text=bits[0], link=bits[1] if len(bits) > 1 else "",
                                                                     days=int(bits[2]) if len(bits) > 2 and bits[2] else None))
            except HTTPException as e:
                return Reply(f"{e.detail}. Исправьте и отправьте ещё раз или /cancel.", state=state)
            except ValueError:
                return Reply("Не разобрал. Формат: Текст | ссылка | дней. Ещё раз или /cancel.", state=state)
            PENDING_INPUT.pop(admin_id, None)
            return banners_view(db, f"✅ Баннер #{banner['id']} добавлен\n\n")
        if kind == "group_link":
            group = db.get(Group, state["id"])
            if group is None or not text.startswith("https://t.me/"):
                return Reply("Ссылка должна начинаться с https://t.me/. Ещё раз или /cancel.", state=state)
            group.telegram_link = text
            log_action(db, actor, "group.update", "group", group.id, text)
            db.commit()
            PENDING_INPUT.pop(admin_id, None)
            return content_view(db, "✅ Ссылка сохранена\n\n")
        if kind == "group_members":
            group = db.get(Group, state["id"])
            if group is None or not text.isdigit():
                return Reply("Нужно целое число. Ещё раз или /cancel.", state=state)
            group.members_count = int(text)
            log_action(db, actor, "group.update", "group", group.id, f"members={text}")
            db.commit()
            PENDING_INPUT.pop(admin_id, None)
            return content_view(db, "✅ Сохранено\n\n")
        if kind == "article_src":
            art = db.get(Article, state["id"])
            if art is None or not text.startswith("https://"):
                return Reply("Нужна ссылка вида https://… Ещё раз или /cancel.", state=state)
            art.source_url, art.checked_at = text[:255], payments.now()
            log_action(db, actor, "article.update", "article", art.id, f"source={text[:80]}")
            db.commit()
            PENDING_INPUT.pop(admin_id, None)
            return content_view(db, "✅ Источник и дата проверки сохранены\n\n")
        if kind == "find_listing":
            if not text.isdigit():
                return Reply("Номер — это число. Отправьте ещё раз или /cancel.", state=state)
            PENDING_INPUT.pop(admin_id, None)
            return listing_card(db, int(text))
        if kind == "listing_hide":
            item = ops.moderate_listing(db, actor, state["id"], "hide", text)
            PENDING_INPUT.pop(admin_id, None)
            reply = listing_card(db, item.id, "🙈 Объявление скрыто\n\n")
            reply.notify.append((item.author_telegram_id, f"Объявление «{_short(item.title, 60)}» скрыто модератором.\nПричина: {item.note}"))
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
    from app.database import SessionLocal
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
    from app import guard
    for tg_id, text in reply.notify:
        with SessionLocal() as db:
            guard.record_notification(db, tg_id, text, "moderation")
            allowed = guard.telegram_allowed(db, tg_id, "moderation")
        if not allowed:
            continue
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
