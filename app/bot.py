import asyncio
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from app.config import settings

async def main():
    if not settings.bot_token:
        print("BOT_TOKEN is empty; bot process is idle. Set BOT_TOKEN to enable Telegram bot.")
        while True:
            await asyncio.sleep(3600)
    bot = Bot(settings.bot_token)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start(message: Message):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть KOREA HUB", web_app=WebAppInfo(url=settings.webapp_url))
        ]])
        await message.answer(
            "🇰🇷 Добро пожаловать в KOREA HUB!\nРабота, жильё и объявления для жизни в Корее.",
            reply_markup=keyboard
        )

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
