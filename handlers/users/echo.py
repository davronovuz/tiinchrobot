import logging
import httpx
import os
import tempfile
from aiogram import types
from aiogram.types import InputFile
from loader import dp, bot, cache_db
import re

# API sozlamalari
RAPIDAPI_KEY = "a89071279emsh52d6dfefe773534p1ef94ejsn4a8c42c2ddb2"
API_URL = "https://auto-download-all-in-one.p.rapidapi.com/v1/social/autolink"
HEADERS = {
    "content-type": "application/json",
    "X-RapidAPI-Key": RAPIDAPI_KEY,
    "X-RapidAPI-Host": "auto-download-all-in-one.p.rapidapi.com"
}

# Admin IDsi
ADMINS = ["1879114908"]

# Logger sozlamalari
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HTTP_URL_REGEXP = r'^(https?://[^\s]+)$'


def get_platform_from_url(url: str) -> str:
    """URL orqali platformani aniqlash."""
    lower_url = url.lower()
    if "instagram.com" in lower_url:
        return "Instagram"
    elif "youtube.com" in lower_url or "youtu.be" in lower_url:
        return "YouTube"
    elif "facebook.com" in lower_url:
        return "Facebook"
    elif "tiktok.com" in lower_url:
        return "TikTok"
    else:
        return "Unknown"


@dp.message_handler(regexp=HTTP_URL_REGEXP)
async def handle_media_request(message: types.Message):
    """Media yuklashni boshqarish."""
    try:
        logger.info(f"Received message from {message.from_user.id}: {message.text}")

        # Yuklanayotganlik xabarini yuborish
        downloading_message = await message.reply("📥 Yuklanmoqda, iltimos kuting...")
        logger.info("Downloading message sent")

        # Adminlarga xabarni forward qilish
        await message.forward(chat_id=ADMINS[0])
        logger.info("Message forwarded to admin")

        # Platformani aniqlash
        platform = get_platform_from_url(message.text)
        cache_db.increment_request_count(platform)

        # API orqali media ma'lumotlarini olish
        response_json = await fetch_media_info(message.text)
        if response_json and response_json.get('medias'):
            logger.info("Media found, processing media...")
            await process_and_send_media(response_json['medias'], message)
        else:
            logger.warning("Media not found or URL is incorrect")
            await message.answer("⛔ Media topilmadi yoki URL noto'g'ri.")

        await downloading_message.delete()
        logger.info("Downloading message deleted")
    except Exception as e:
        logger.error(f"Error handling media request: {e}")
        await message.answer("⚠️ Xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")


async def fetch_media_info(url: str) -> dict:
    """API orqali media haqida ma'lumot olish."""
    try:
        logger.info(f"Fetching media info for URL: {url}")
        async with httpx.AsyncClient() as client:
            response = await client.post(API_URL, json={"url": url}, headers=HEADERS)
            if response.status_code == 200:
                logger.info("Media info fetched successfully")
                return response.json()
            else:
                logger.warning(f"Failed to fetch media info, status code: {response.status_code}")
                return {}
    except Exception as e:
        logger.error(f"Error fetching media info: {e}")
        return {}


async def process_and_send_media(medias: list, message: types.Message):
    """Mediadan yuklab olish va foydalanuvchiga jo'natish."""
    try:
        for media in medias:
            media_url = media.get('url')
            media_format = media.get('type')
            logger.info(f"Processing media: URL={media_url}, format={media_format}")

            # DB dan file_id ni tekshirish
            file_id = cache_db.get_file_id_by_url(media_url)
            if file_id:
                # Keshdan foydalanish
                logger.info(f"Media found in DB cache with file_id: {file_id}")
                await send_media(file_id, media_format, message)
            else:
                # Yuklab olish
                downloaded_file = await download_media(media_url, media_format)
                if downloaded_file:
                    msg = await send_media(downloaded_file, media_format, message)
                    cache_db.add_cache("unknown", media_url, msg)
                    os.unlink(downloaded_file)
                else:
                    await message.answer("❌ Media yuklab olinmadi.")
    except Exception as e:
        logger.error(f"Error processing media: {e}")


async def send_media(file, media_format, message):
    """Media jo'natish."""
    caption = (
        "✨ @tinchrobot – <b>Tinchlikni xohlovchilar uchun!</b> ️\n"

    )
    if media_format == "image":
        return await message.answer_photo(InputFile(file), caption=caption, parse_mode="HTML")
    elif media_format == "video":
        return await message.answer_video(InputFile(file), caption=caption, parse_mode="HTML")
    elif media_format == "audio":
        return await message.answer_audio(InputFile(file), caption=caption, parse_mode="HTML")
    else:
        await message.answer("⚠️ Qo'llab-quvvatlanmaydigan media turi.")


async def download_media(media_url: str, media_format: str) -> str:
    """Media yuklab olish."""
    try:
        logger.info(f"Downloading media from URL: {media_url}")
        async with httpx.AsyncClient() as client:
            response = await client.get(media_url)
            if response.status_code == 200:
                suffix = {"image": ".jpg", "video": ".mp4", "audio": ".mp3"}.get(media_format, ".dat")
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
                    tmp_file.write(response.content)
                    return tmp_file.name
            else:
                logger.warning(f"Failed to download media, status code: {response.status_code}")
                return ""
    except Exception as e:
        logger.error(f"Error downloading media: {e}")
        return ""
