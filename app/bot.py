import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import (
    Message, PreCheckoutQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo,
    MenuButtonWebApp,
)
from sqlalchemy import select

from app import payments
from app.config import settings
from app.database import SessionLocal
from app.models import Listing

WELCOME = "🇰🇷 Добро пожаловать в KOREA HUB!\nРабота, жильё и объявления для жизни в Корее."
HELP_TEXT = (
    "Команды:\n"
    "/start — открыть приложение\n"
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


def menu_markup() -> InlineKeyboardMarkup:
    base = settings.webapp_url.rstrip("/")
    rows = []
    for i in range(0, len(MENU_SECTIONS), 2):
        row = [InlineKeyboardButton(text=label, web_app=WebAppInfo(url=f"{base}/?section={key}"))
               for label, key in MENU_SECTIONS[i:i + 2]]
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def moderate(listing_id: int, decision: str, reason: str = "") -> tuple[Listing | None, str]:
    """Change status of a pending listing. Returns (listing, error message)."""
    with SessionLocal() as db:
        item = db.get(Listing, listing_id)
        if item is None:
            return None, f"Объявление #{listing_id} не найдено."
        if item.status != "pending":
            return item, f"Объявление #{listing_id} уже {STATUS_RU.get(item.status, item.status)}."
        item.status = "approved" if decision == "approve" else "rejected"
        db.commit()
        db.refresh(item)
        return item, ""


def register(dp: Dispatcher, bot: Bot) -> None:
    @dp.message(CommandStart())
    async def start(message: Message):
        await message.answer(WELCOME + "\nВыберите нужный раздел:", reply_markup=menu_markup())

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
    """End finished placements. Returns (user_id, message) pairs to notify."""
    with SessionLocal() as db:
        ended = payments.expire_placements(db)
        messages = []
        for placement in ended:
            listing = db.get(Listing, placement.listing_id)
            if listing is None or not listing.author_telegram_id.isdigit():
                continue
            messages.append((listing.author_telegram_id,
                             f"Срок услуги «{placement.kind}» для #{listing.id} {listing.title} закончился. "
                             "Продлить можно в приложении: Профиль → Мои объявления."))
        return messages


async def expiry_loop(bot: Bot) -> None:
    while True:
        try:
            for user_id, text in await asyncio.to_thread(_expire_once):
                try:
                    await bot.send_message(int(user_id), text)
                except Exception:
                    pass
        except Exception:
            pass
        await asyncio.sleep(EXPIRY_CHECK_SECONDS)


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
    await dp.start_polling(bot, allowed_updates=["message", "pre_checkout_query"])


if __name__ == "__main__":
    asyncio.run(main())
