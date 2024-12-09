from aiogram import types
from aiogram.dispatcher.filters.builtin import CommandStart
from loader import dp, user_db

@dp.message_handler(CommandStart())
async def bot_start(message: types.Message):
    telegram_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    # Foydalanuvchi bazada mavjudligini tekshirish
    user = user_db.select_user(telegram_id=telegram_id)
    if not user:
        # Yangi foydalanuvchini bazaga qo'shish
        user_db.add_user(telegram_id=telegram_id, username=username)
        welcome_text = (
            f"🌟 Salom va xush kelibsiz, {message.from_user.full_name}! 🎉\n\n"
            "🤖 <b>Tinchrobot</b> – <i>'Maksimal tinchlikni xohlovchilar uchun'</i> 🕊️✨\n\n"
            "🚀 Sizni kutayotgan xizmatlar:\n"
            "📸 Instagram: Postlar, Reels, Stories yuklash\n"
            "🎵 TikTok: Videolar va Musiqalar yuklash\n"
            "🎯 Havolani yuboring va dam oling – biz hammasini hal qilamiz! 😌\n\n"
            "👉 <i>Maksimal tinchlikni saqlash uchun – @tinchrobot</i>ni tanlang! 🕊️"
        )
        await message.answer(welcome_text, parse_mode="HTML")
    else:
        # Foydalanuvchini qayta kelganligini yangilash va xush kelibsiz xabarini yuborish
        user_db.update_user_last_active(telegram_id=telegram_id)
        welcome_back_text = (
            f"🌟 Assalomu alaykum, {message.from_user.full_name}! 🎉\n\n"
            "🤖 Sizni yana <b>Tinchrobot</b>da ko'rishdan xursandmiz! 🕊️\n"
            "🎯 Faqat havolani yuboring va biz kontentingizni tezda yuklaymiz. 🚀\n\n"
            "🕊️ Maksimal tinchlikni xohlovchilar uchun –  @tinchrobot xizmatda 😊"
        )
        await message.answer(welcome_back_text, parse_mode="HTML")
