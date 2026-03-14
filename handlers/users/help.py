from aiogram import types
from aiogram.dispatcher.filters.builtin import CommandHelp

from loader import dp


@dp.message_handler(CommandHelp())
async def bot_help(message: types.Message):
    text = (
        "📋 <b>Buyruqlar:</b>\n\n"
        "/start - Botni ishga tushirish\n"
        "/help - Yordam\n"
        "/top - Top 10 musiqalar\n"
        "/new - Yangi musiqalar\n"
        "/tiktok - TikTok top musiqalar\n\n"
        "🎯 <b>Imkoniyatlar:</b>\n\n"
        "🔍 <b>Musiqa qidirish</b> — matn yozing, YouTube dan topamiz\n"
        "🎵 <b>Shazam</b> — ovozli xabar yuboring, musiqani aniqlaymiz\n"
        "🎬 <b>Video yuklash</b> — havola yuboring (Instagram, TikTok, YouTube...)\n"
        "🎶 <b>Videodagi musiqa</b> — yuklangan videodagi musiqani aniqlaymiz\n\n"
        "🕊️ @tinchrobot — Tinchlikni xohlovchilar uchun!"
    )
    await message.answer(text, parse_mode="HTML")