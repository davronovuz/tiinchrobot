import logging
import os
import re
from aiogram import types
from aiogram.types import InputFile
from aiogram.utils.markdown import html_decoration as hd
from loader import dp, bot, cache_db
from utils.video_downloader import download_video, cleanup_file, is_supported_url, get_platform_from_url
from utils.pyrogram_client import send_large_video, pyro_client

FILE_SIZE_LIMIT = 50 * 1024 * 1024  # 50MB (aiogram limiti)
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB (pyrogram limiti)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HTTP_URL_REGEXP = r'^(https?://[^\s]+)$'


@dp.message_handler(regexp=HTTP_URL_REGEXP)
async def handle_media_request(message: types.Message):
    url = message.text.strip()

    if not is_supported_url(url):
        return

    platform = get_platform_from_url(url)
    user_id = message.from_user.id
    logger.info(f"User: {user_id}, URL: {url}, Platform: {platform}")

    # Statistika
    await cache_db.increment_request_count(platform)

    # Cache tekshirish
    cached = await cache_db.get_file_id_by_url(url)
    if cached:
        await send_cached_media(message, cached["file_id"], cached["media_type"])
        return

    downloading_message = await message.reply("📥 Yuklanmoqda, iltimos kuting...")

    try:
        # yt-dlp orqali yuklab olish
        result = await download_video(url)

        if not result or not result.get('file_path'):
            await downloading_message.edit_text("⛔ Media topilmadi yoki yuklab olishda xatolik yuz berdi.")
            return

        file_path = result['file_path']
        file_size = result['filesize']
        title = result.get('title', 'Video')
        duration = result.get('duration', 0)
        width = result.get('width', 0)
        height = result.get('height', 0)

        caption = f"✨ {hd.bold('@tinchrobot')} – Tinchlikni xohlovchilar uchun!"

        if file_size > MAX_FILE_SIZE:
            await downloading_message.edit_text(
                f"⛔ Fayl hajmi juda katta (>{MAX_FILE_SIZE // (1024*1024*1024)}GB). "
                f"Telegram limiti tufayli yuborib bo'lmaydi."
            )
            cleanup_file(file_path)
            return

        if file_size > FILE_SIZE_LIMIT:
            # Katta fayl — Pyrogram MTProto orqali yuboramiz (2GB gacha)
            if pyro_client:
                await downloading_message.edit_text("📤 Katta fayl yuborilmoqda (MTProto)...")
                file_id = await send_large_video(
                    chat_id=message.chat.id,
                    file_path=file_path,
                    caption=caption,
                    duration=duration,
                    width=width,
                    height=height,
                )
                if file_id:
                    await cache_db.add_cache(platform, url, file_id, "video")
                    await downloading_message.delete()
                else:
                    await downloading_message.edit_text("⚠️ Katta faylni yuborishda xatolik.")
            else:
                await downloading_message.edit_text(
                    f"📎 Video hajmi katta ({file_size // (1024*1024)}MB). "
                    f"Pyrogram sozlanmagan, katta fayllar yuborilmaydi."
                )
        else:
            # Oddiy fayl — aiogram orqali yuboramiz (<50MB)
            input_file = InputFile(file_path)
            sent_msg = await message.answer_video(
                input_file,
                caption=caption,
                parse_mode="HTML",
                duration=duration,
                width=width,
                height=height,
                supports_streaming=True,
            )
            if sent_msg.video:
                await cache_db.add_cache(platform, url, sent_msg.video.file_id, "video")
            await downloading_message.delete()

        cleanup_file(file_path)

    except Exception as e:
        logger.error(f"Xatolik: {e}")
        try:
            await downloading_message.edit_text("⚠️ Xatolik yuz berdi. Qayta urinib ko'ring.")
        except Exception:
            pass


async def send_cached_media(message: types.Message, file_id: str, media_type: str = "document"):
    caption = f"✨ {hd.bold('@tinchrobot')} – Tinchlikni xohlovchilar uchun!"
    if media_type == "video":
        await message.answer_video(file_id, caption=caption, parse_mode="HTML")
    elif media_type == "audio":
        await message.answer_audio(file_id, caption=caption, parse_mode="HTML")
    elif media_type == "photo":
        await message.answer_photo(file_id, caption=caption, parse_mode="HTML")
    else:
        await message.answer_document(file_id, caption=caption, parse_mode="HTML")
