import asyncio

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from sqlalchemy import select

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
}
CATEGORY_RU = {"job": "Работа", "housing": "Жильё", "marketplace": "Барахолка"}


def admin_ids() -> set[int]:
    return {int(x) for x in settings.admin_ids.split(",") if x.strip().isdigit()}


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in admin_ids()


def open_app_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть KOREA HUB", web_app=WebAppInfo(url=settings.webapp_url))
    ]])


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
        await message.answer(WELCOME, reply_markup=open_app_markup())

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


async def notify_author(bot: Bot, item: Listing, text: str) -> None:
    if not item.author_telegram_id.isdigit():
        return
    try:
        await bot.send_message(int(item.author_telegram_id), f"#{item.id} {item.title}\n{text}")
    except Exception:
        # Пользователь мог заблокировать бота. Модерация уже сохранена, поэтому ошибку не поднимаем.
        pass


async def main():
    if not settings.bot_token:
        print("BOT_TOKEN is empty; bot process is idle. Set BOT_TOKEN to enable Telegram bot.")
        while True:
            await asyncio.sleep(3600)
    bot = Bot(settings.bot_token)
    # Бот работает через polling. Если у токена активен webhook (например, остался
    # от другого сервиса), Telegram не отдаёт обновления и /start молчит.
    await bot.delete_webhook(drop_pending_updates=True)
    dp = Dispatcher()
    register(dp, bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
