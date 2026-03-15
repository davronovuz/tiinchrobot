from aiogram import types
from aiogram.dispatcher.filters.builtin import CommandStart
from loader import dp, user_db

@dp.message_handler(CommandStart())
async def bot_start(message: types.Message):
    telegram_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    user = await user_db.select_user(telegram_id=telegram_id)
    if not user:
        await user_db.add_user(telegram_id=telegram_id, username=username)

    if user:
        await user_db.update_user_last_active(telegram_id=telegram_id)

    welcome_text = (
        f"Salom, <b>{message.from_user.full_name}</b>\n\n"
        "<b>Tinch Robot</b> — tinch ishlaydi.\n\n"
        "Havolani tashlang, qolgani bizda:\n\n"
        "  YouTube\n"
        "  Instagram\n"
        "  TikTok\n"
        "  Pinterest\n"
        "  Snapchat\n"
        "  Facebook\n"
        "  Twitter / X\n\n"
        "Musiqa qidirish — qo'shiq nomini yozing.\n"
        "Musiqa aniqlash — audio/video yuboring.\n\n"
        "<i>Havola yuboring — natija olasiz.</i>"
    )
    await message.answer(welcome_text, parse_mode="HTML")
