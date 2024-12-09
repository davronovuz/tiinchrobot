import logging
import httpx
import os
import tempfile
from aiogram import types
from aiogram.types import InputFile, MediaGroup
from aiogram.utils import executor
from loader import dp, bot,cache_db
import re
import hashlib
import json


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
    # Bu yerda oddiy bir moslashuv:
    # Instagram, Youtube, Facebook va hokazo domenlar aniqlanadi
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
    """Media so'rovlarini boshqarish"""
    try:
        logger.info(f"Received message from {message.from_user.id}: {message.text}")

        # Yuklanayotganlik stikerini jo'natish
        downloading_message = await message.reply_sticker(
            "CAACAgEAAxkBAAMWZ0Hqfhu6E-BQLDjgWC2B0Dg_QpsAAoACAAKhYxlEq1g_ogXCTdw2BA"
        )
        logger.info("Downloading sticker sent")

        # Adminlarga xabarni forward qilish
        await message.forward(chat_id=ADMINS[0])
        logger.info("Message forwarded to admin")

        # Platformani aniqlash va statistikani yangilash
        platform = get_platform_from_url(message.text)
        cache_db.increment_request_count(platform)

        # API orqali ma'lumot olish
        response_json = await fetch_media_info(message.text)
        if response_json and response_json.get('medias'):
            logger.info("Media found, processing media...")
            await process_and_send_media(response_json['medias'], message, platform)
        else:
            logger.warning("Media not found or URL is incorrect")
            await message.answer("Media topilmadi yoki URL noto'g'ri.")

        await downloading_message.delete()
        logger.info("Downloading sticker deleted")
    except Exception as e:
        logger.error(f"Error handling media request: {e}")
        await message.answer("Xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")

async def fetch_media_info(url: str) -> dict:
    """API orqali media haqida ma'lumot olish"""
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

async def process_and_send_media(medias: list, message: types.Message, platform: str):
    """Media fayllarini yuklab olish va foydalanuvchiga jo'natish"""
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
                send_method = {
                    "image": message.answer_photo,
                    "video": message.answer_video,
                    "audio": message.answer_audio
                }.get(media_format, None)
                if send_method:
                    await send_method(file_id, caption="\nObuna talab qilmaydigan yagona tezkor bot – @ufastbot | TEZKOR YUKLASH")
                    logger.info(f"Media sent to user from cache: {message.from_user.id}")
                else:
                    logger.warning(f"Unsupported media type: {media_format}")
                    await message.answer("Qo'llab-quvvatlanmaydigan media turi.")
            else:
                # Agar keshda bo'lmasa yuklab olamiz
                if media_format == 'image':
                    image = await download_media(media_url, media_format)
                    if image:
                        msg = await message.answer_photo(InputFile(image), caption="\nObuna talab qilmaydigan yagona tezkor bot – @ufastbot | TEZKOR YUKLASH")
                        file_id = msg.photo[-1].file_id
                        # DB ga kiritish
                        cache_db.add_cache(platform, media_url, file_id)
                        logger.info(f"Media downloaded and sent, file_id cached in DB: {file_id}")
                        # Yuklab olingan faylni o'chirish
                        os.unlink(image)
                    else:
                        await message.answer("Media yuklab olinmadi.")
                elif media_format in ['video', 'audio']:
                    await download_and_send_media(media_url, media_format, message, platform)
                else:
                    logger.warning(f"Unsupported media type: {media_format}")
                    await message.answer("Qo'llab-quvvatlanmaydigan media turi.")
    except Exception as e:
        logger.error(f"Error processing media: {e}")

async def download_media(media_url: str, media_format: str) -> str:
    """Mediadan yuklab olish"""
    try:
        logger.info(f"Downloading media from URL: {media_url}")
        async with httpx.AsyncClient() as client:
            response = await client.get(media_url)
            if response.status_code == 200:
                suffix = {"image": ".jpg"}.get(media_format, ".dat")
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
                    tmp_file.write(response.content)
                    tmp_file_path = tmp_file.name
                logger.info(f"Media downloaded and saved to temporary file: {tmp_file_path}")
                return tmp_file_path
            else:
                logger.warning(f"Failed to download media, status code: {response.status_code}")
                return ""
    except Exception as e:
        logger.error(f"Error downloading media: {e}")
        return ""

async def download_and_send_media(media_url: str, media_format: str, message: types.Message, platform: str):
    """Mediadan yuklab olish va foydalanuvchiga jo'natish"""
    try:
        # DB dan file_id ni tekshirish
        file_id = cache_db.get_file_id_by_url(media_url)
        if file_id:
            logger.info(f"Media found in DB cache with file_id: {file_id}")
            send_method = {
                "video": message.answer_video,
                "audio": message.answer_audio
            }.get(media_format, None)
            if send_method:
                await send_method(file_id, caption="\nObuna talab qilmaydigan yagona tezkor bot – @ufastbot | TEZKOR YUKLASH")
                logger.info(f"Media sent to user from cache: {message.from_user.id}")
            else:
                logger.warning(f"Unsupported media type: {media_format}")
                await message.answer("Qo'llab-quvvatlanmaydigan media turi.")
        else:
            logger.info(f"Downloading media from URL: {media_url}")
            async with httpx.AsyncClient() as client:
                response = await client.get(media_url)
                if response.status_code == 200:
                    suffix = {"video": ".mp4", "audio": ".mp3"}.get(media_format, ".dat")
                    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
                        tmp_file.write(response.content)
                        tmp_file_path = tmp_file.name
                    logger.info(f"Media downloaded and saved to temporary file: {tmp_file_path}")

                    send_method = {
                        "video": message.answer_video,
                        "audio": message.answer_audio
                    }.get(media_format, None)

                    if send_method:
                        msg = await send_method(InputFile(tmp_file_path), caption="\nObuna talab qilmaydigan yagona tezkor bot – @ufastbot | TEZKOR YUKLASH")
                        logger.info(f"Media sent to user: {message.from_user.id}")
                        # file_id ni olish
                        if media_format == 'video':
                            file_id = msg.video.file_id
                        elif media_format == 'audio':
                            file_id = msg.audio.file_id
                        # DB ga kiritish
                        cache_db.add_cache(platform, media_url, file_id)
                        logger.info(f"file_id cached in DB: {file_id}")
                        # Yuklab olingan faylni o'chirish
                        os.unlink(tmp_file_path)
                    else:
                        logger.warning(f"Unsupported media type: {media_format}")
                        await message.answer("Qo'llab-quvvatlanmaydigan media turi.")
                else:
                    logger.warning(f"Failed to download media, status code: {response.status_code}")
                    await message.answer("Media yuklab olinmadi.")
    except Exception as e:
        logger.error(f"Error downloading and sending media: {e}")
