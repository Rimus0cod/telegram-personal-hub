import asyncio
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from config import BOT_TOKEN, WEBAPP_URL

router=Router()
@router.message(CommandStart())
async def start(message:Message):
    keyboard=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Open Personal Hub",web_app=WebAppInfo(url=WEBAPP_URL))]])
    await message.answer(f"Hi {message.from_user.first_name}! Your personal space for weather, notes, tasks, and focus.",reply_markup=keyboard)
@router.message(Command("help"))
async def help_command(message:Message):
    await message.answer("Open your Personal Hub with /start. Use the Mini App to manage your notes, tasks, weather, and focus sessions.")
async def main():
    bot=Bot(BOT_TOKEN); dp=Dispatcher(); dp.include_router(router)
    await dp.start_polling(bot)
if __name__=="__main__": asyncio.run(main())
