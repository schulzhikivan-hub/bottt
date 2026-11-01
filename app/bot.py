import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import (
    Message, PreCheckoutQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo,
    MenuButtonWebApp, CallbackQuery,
)
from sqlalchemy import select

from app import guard, lifecycle, observe, payments, searches, webauth
from app.botadmin import register_admin
from app.botbrowse import register_browse
from app.config import settings
from app.database import SessionLocal
from app.models import Application, Listing, Admin

WELCOME = "🇰🇷 Добро пожаловать в KOREA HUB!\nРабота, жильё, знакомства и полезная информация для жизни в Корее."
HELP_TEXT = (
    "Команды:\n"
    "/start — разделы: работа, жильё, барахолка, знакомства, сообщество, гид (смотреть можно прямо в чате)\n"
    "/find слово — поиск по всем разделам\n"
    "/my — мои объявления и их статус\n"
    "/help — эта справка\n\n"
    "Для администраторов:\n"
    "/pending — объявления на модерации\n"
    "/approve <id> — одобрить\n"
    "/reject <id> <причина> — отклонить с причиной"
)
STATUS_RU = {
    "pending": "на модерации",
    "approved": "опубликовано",
    "rejected": "отклонено",
    "hidden": "скрыто",
}
CATEGORY_RU = {"job": "Работа", "housing": "Жильё", "marketplace": "Барахолка"}
EXPIRY_CHECK_SECONDS = 300


def admin_ids() -> set[int]:
    return {int(x) for x in settings.admin_ids.split(",") if x.strip().isdigit()}


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in admin_ids()


def open_app_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть KOREA HUB", web_app=WebAppInfo(url=settings.webapp_url))
    ]])


# Разделы меню бота (ТЗ § 5.1). Mini App открывает нужный раздел по параметру ?section=
MENU_SECTIONS = [
    ("💼 Работа", "job"),
    ("🏠 Жильё", "housing"),
    ("🛍️ Барахолка", "marketplace"),
    ("💗 Знакомства", "dating"),
    ("👥 Сообщество", "community"),
    ("📘 Гид", "guide"),
]


def is_staff(tg_id: int) -> bool:
    with SessionLocal() as db:
        admin = db.get(Admin, str(tg_id))
        return admin is not None and admin.active


def admin_button() -> list[InlineKeyboardButton]:
    # Админка открывается в этом же чате, без сайта
    return [InlineKeyboardButton(text="🛠 Админ-панель", callback_data="a:menu")]


def menu_markup(staff: bool = False) -> InlineKeyboardMarkup:
    """Sections open right here in the chat (see botbrowse); the Mini App button is for writing and adding."""
    from app import botbrowse
    markup = botbrowse._markup(botbrowse.home_view().rows)
    if staff:
        markup.inline_keyboard.append(admin_button())
    return markup


def moderate(listing_id: int, decision: str, reason: str = "") -> tuple[Listing | None, str]:
    """Commands /approve and /reject use the same rules as the admin panel (photos, lifetime, journal)."""
    from app import admin_ops as ops
    with SessionLocal() as db:
        current = db.get(Listing, listing_id)
        if current is None:
            return None, f"Объявление #{listing_id} не найдено."
        if current.status != "pending" and not (current.status == "approved" and current.has_edit):
            return current, f"Объявление #{listing_id} уже {STATUS_RU.get(current.status, current.status)}."
        try:
            item = ops.moderate_listing(db, "bot-command", listing_id,
                                        "approve" if decision == "approve" else "reject", reason or "Отклонено модератором")
        except ops.OpError as e:
            return None, e.message
        db.refresh(item)
        return item, ""


def _remember_source(tg_id: str, tag: str) -> None:
    """Advertising tag from the start link. Only the first one counts; junk is ignored."""
    import re
    from app.models import ReferralSource
    if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", tag):
        return
    with SessionLocal() as db:
        if db.get(ReferralSource, tg_id) is None:
            db.add(ReferralSource(tg_id=tg_id, source=tag))
            db.commit()


def register(dp: Dispatcher, bot: Bot) -> None:
    @dp.message(CommandStart(deep_link=True))
    async def start_deeplink(message: Message, command: CommandObject):
        arg = command.args or ""
        if arg.startswith("l_") and arg[2:].isdigit():
            from app import botbrowse
            with SessionLocal() as db:
                view = botbrowse.single_view(db, int(arg[2:]))
            await botbrowse.send_view(bot, message.chat.id, view)
            return
        if arg.startswith("src_"):
            _remember_source(str(message.from_user.id), arg[4:])
        if not arg.startswith("login_"):
            await message.answer(WELCOME + "\nВыберите нужный раздел:",
                                 reply_markup=menu_markup(is_staff(message.from_user.id)))
            return
        token = arg[len("login_"):]
        with SessionLocal() as db:
            row = webauth.login_request(db, token)
            details = None if row is None else (row.ip or "неизвестно", row.agent or "неизвестно")
        if details is None:
            await message.answer("Ссылка для входа устарела. Нажмите «Войти через Telegram» на сайте ещё раз.")
            return
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Это я, войти", callback_data=f"lc:{token}"),
            InlineKeyboardButton(text="❌ Это не я", callback_data=f"ln:{token}"),
        ]])
        await message.answer(
            "Кто-то хочет войти на сайт KOREA HUB от вашего имени.\n"
            f"IP: {details[0]}\nБраузер: {details[1]}\n\n"
            "Подтверждайте, только если это вы только что нажали «Войти через Telegram».",
            reply_markup=markup)

    @dp.callback_query(F.data.startswith("lc:") | F.data.startswith("ln:"))
    async def login_decision(query: CallbackQuery):
        action, _, token = query.data.partition(":")
        with SessionLocal() as db:
            if action == "lc":
                ok = webauth.confirm_login(db, token, str(query.from_user.id))
            else:
                ok = webauth.reject_login(db, token)
        if action == "lc" and ok:
            text = "Вход подтверждён ✅ Вернитесь на сайт, он откроется сам."
        elif action == "ln" and ok:
            text = "Вход отклонён. Если это были не вы, ничего делать не нужно."
        else:
            text = "Ссылка устарела. Нажмите «Войти через Telegram» на сайте ещё раз."
        await query.message.edit_text(text)
        await query.answer()

    @dp.message(CommandStart())
    async def start(message: Message):
        await message.answer(WELCOME + "\nВыберите нужный раздел:",
                             reply_markup=menu_markup(is_staff(message.from_user.id)))

    @dp.message(Command("help"))
    async def help_cmd(message: Message):
        await message.answer(HELP_TEXT)

    @dp.message(Command("my"))
    async def my_listings(message: Message):
        with SessionLocal() as db:
            items = db.scalars(
                select(Listing)
                .where(Listing.author_telegram_id == str(message.from_user.id))
                .order_by(Listing.id.desc())
                .limit(20)
            ).all()
        if not items:
            await message.answer("У вас пока нет объявлений. Добавьте их в приложении.", reply_markup=open_app_markup())
            return
        lines = [f"#{x.id} {x.title} — {STATUS_RU.get(x.status, x.status)}" for x in items]
        await message.answer("\n".join(lines))

    @dp.message(Command("pending"))
    async def pending(message: Message):
        if not is_admin(message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return
        with SessionLocal() as db:
            items = db.scalars(
                select(Listing).where(Listing.status == "pending").order_by(Listing.id).limit(30)
            ).all()
        if not items:
            await message.answer("Очередь модерации пуста.")
            return
        lines = [
            f"#{x.id} [{CATEGORY_RU.get(x.category, x.category)}] {x.title} — {x.city}"
            for x in items
        ]
        await message.answer("На модерации:\n" + "\n".join(lines) + "\n\n/approve <id> или /reject <id> <причина>")

    @dp.message(Command("approve"))
    async def approve(message: Message, command: CommandObject):
        if not is_admin(message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return
        if not command.args or not command.args.strip().isdigit():
            await message.answer("Формат: /approve <id>")
            return
        item, error = moderate(int(command.args), "approve")
        if error:
            await message.answer(error)
            return
        await message.answer(f"Объявление #{item.id} опубликовано.")
        await notify_author(bot, item, "Ваше объявление одобрено и опубликовано в KOREA HUB.")

    @dp.message(Command("reject"))
    async def reject(message: Message, command: CommandObject):
        if not is_admin(message.from_user.id):
            await message.answer("Команда доступна только администраторам.")
            return
        parts = (command.args or "").split(maxsplit=1)
        if not parts or not parts[0].isdigit() or len(parts) < 2:
            await message.answer("Формат: /reject <id> <причина>")
            return
        item, error = moderate(int(parts[0]), "reject", parts[1])
        if error:
            await message.answer(error)
            return
        await message.answer(f"Объявление #{item.id} отклонено.")
        await notify_author(bot, item, f"Ваше объявление отклонено. Причина: {parts[1]}")

    register_browse(dp, bot)  # before the admin text handler, so a search word is not swallowed
    register_admin(dp, bot)

    # ---- Paid services (ТЗ № 2) ----

    @dp.pre_checkout_query()
    async def pre_checkout(query: PreCheckoutQuery):
        # Telegram requires an answer within 10 seconds; the check is a quick DB lookup.
        with SessionLocal() as db:
            error = payments.check_pre_checkout(
                db, query.invoice_payload, str(query.from_user.id), query.currency, query.total_amount)
        await bot.answer_pre_checkout_query(query.id, ok=error is None, error_message=error)

    @dp.message(F.successful_payment)
    async def paid(message: Message):
        sp = message.successful_payment
        user_id = str(message.from_user.id)
        try:
            with SessionLocal() as db:
                placement = payments.process_successful_payment(
                    db, user_id, sp.invoice_payload, sp.telegram_payment_charge_id,
                    sp.currency, sp.total_amount)
                ends = payments.aware(placement.ends_at).strftime("%d.%m.%Y %H:%M")
            await message.answer(f"Оплата получена. Услуга активна до {ends} UTC.")
        except payments.PaymentError as exc:
            # Money was taken but the service cannot be given: return the stars automatically (ТЗ § 10.5)
            try:
                await asyncio.to_thread(payments.telegram_call, "refundStarPayment", {
                    "user_id": message.from_user.id,
                    "telegram_payment_charge_id": sp.telegram_payment_charge_id,
                })
                with SessionLocal() as db:
                    payments.mark_failed(db, sp.invoice_payload)
                await message.answer(f"Услуга не активирована ({exc}). Оплата возвращена.")
            except Exception:
                await message.answer(
                    f"Услуга не активирована ({exc}). Возврат не прошёл автоматически. "
                    "Напишите в поддержку с номером платежа: " + sp.telegram_payment_charge_id)


async def notify_author(bot: Bot, item: Listing, text: str) -> None:
    if not item.author_telegram_id.isdigit():
        return
    try:
        await bot.send_message(int(item.author_telegram_id), f"#{item.id} {item.title}\n{text}")
    except Exception:
        # Пользователь мог заблокировать бота. Модерация уже сохранена, поэтому ошибку не поднимаем.
        pass


def _expire_once() -> list[tuple[str, str]]:
    """End finished placements and listings, warn about listings ending soon. Returns (user_id, message) pairs."""
    with SessionLocal() as db:
        messages = []  # (user, text, kind)
        for item in lifecycle.expiring_soon(db):
            messages.append((item.author_telegram_id,
                             f"Объявление «{item.title}» закончится через несколько дней. "
                             "Продлите его: Профиль → Мои объявления → Продлить."))
        for item in lifecycle.expiring_tomorrow(db):
            messages.append((item.author_telegram_id,
                             f"Завтра закончится срок объявления «{item.title}». Продлить можно в один шаг: "
                             "Профиль → Мои объявления → Продлить.", "expiry"))
        for item in lifecycle.expire_listings(db):
            messages.append((item.author_telegram_id,
                             f"Срок объявления «{item.title}» истёк, его больше не видно в поиске. "
                             "Продлить можно в приложении: Профиль → Мои объявления.", "expiry"))
            if item.category == "job":
                for row in db.scalars(select(Application).where(Application.listing_id == item.id,
                                                                Application.status.in_(["new", "viewed", "invited"]))).all():
                    messages.append((row.applicant_tg_id, f"Вакансия «{item.title}» закрыта: срок объявления истёк.", "applications"))
        ended = payments.expire_placements(db)
        for placement in ended:
            listing = db.get(Listing, placement.listing_id)
            if listing is None or not listing.author_telegram_id.isdigit():
                continue
            messages.append((listing.author_telegram_id,
                             f"Срок услуги «{placement.kind}» для #{listing.id} {listing.title} закончился. "
                             "Продлить можно в приложении: Профиль → Мои объявления.", "payments"))
        for placement in payments.placements_ending_soon(db):
            listing = db.get(Listing, placement.listing_id)
            if listing is not None and listing.author_telegram_id.isdigit():
                messages.append((listing.author_telegram_id,
                                 f"Завтра закончится «{placement.kind}» для «{listing.title}». Продлить можно после окончания: "
                                 "Профиль → Мои объявления → Продвинуть.", "payments"))
        messages.extend((tg, text, "searches") for tg, text in searches.new_matches(db))
        # every notice goes to the in-app feed; Telegram only gets what the user allows
        out = []
        for tg, text, kind in messages:
            guard.record_notification(db, tg, text, kind)
            if guard.telegram_allowed(db, tg, kind):
                out.append((tg, text))
        return out


def _heartbeat() -> None:
    with SessionLocal() as db:
        observe.beat(db, "bot")
        guard.purge_rate_hits(db)       # old rate-limit counters


async def expiry_loop(bot: Bot) -> None:
    failures = 0
    while True:
        try:
            await asyncio.to_thread(_heartbeat)       # "the bot is alive": /health reports it
            for user_id, text in await asyncio.to_thread(_expire_once):
                try:
                    await bot.send_message(int(user_id), text)
                except Exception:
                    pass
            failures = 0
        except Exception as exc:  # noqa: BLE001
            failures += 1
            observe.event(40, "expiry loop failed", error=type(exc).__name__, count=failures)
            if failures == 5:
                observe.alert_admins("фоновые задачи бота (сроки, уведомления) не работают: 5 ошибок подряд")
        await asyncio.sleep(EXPIRY_CHECK_SECONDS)


async def broadcast_loop(bot: Bot) -> None:
    """Send running broadcasts slowly: one batch every few seconds, so Telegram limits are never hit."""
    from app import adminx
    while True:
        try:
            tried = await asyncio.to_thread(_broadcast_once, adminx)
        except Exception:
            tried = 0
        await asyncio.sleep(2 if tried else 10)


def _broadcast_once(adminx) -> int:
    with SessionLocal() as db:
        return adminx.broadcast_tick(db, guard.send_telegram)


async def main():
    if not settings.bot_token:
        print("BOT_TOKEN is empty; bot process is idle. Set BOT_TOKEN to enable Telegram bot.")
        while True:
            await asyncio.sleep(3600)
    bot = Bot(settings.bot_token)
    # Бот работает через polling. Если у токена активен webhook (например, остался
    # от другого сервиса), Telegram не отдаёт обновления и /start молчит.
    await bot.delete_webhook(drop_pending_updates=True)
    # Кнопка меню рядом с полем ввода открывает приложение внутри Telegram, а не в браузере
    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
        text="KOREA HUB", web_app=WebAppInfo(url=settings.webapp_url)))
    dp = Dispatcher()
    register(dp, bot)
    asyncio.create_task(expiry_loop(bot))
    asyncio.create_task(broadcast_loop(bot))
    observe.setup_logging()
    observe.event(20, "bot started")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "pre_checkout_query", "callback_query"])
    except Exception as exc:  # noqa: BLE001
        observe.event(40, "bot stopped", error=type(exc).__name__)
        observe.alert_admins("бот остановился и перезапускается (" + type(exc).__name__ + ")")
        raise


if __name__ == "__main__":
    asyncio.run(main())
