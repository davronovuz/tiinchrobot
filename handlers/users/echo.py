import logging
import httpx
import os
import tempfile
import subprocess
from aiogram import types
from aiogram.types import InputFile, MediaGroup
from loader import dp, bot, cache_db
from shazamio import Shazam

# API sozlamalari
RAPIDAPI_KEY = "a89071279emsh52d6dfefe773534p1ef94ejsn4a8c42c2ddb2"
API_URL = "https://auto-download-all-in-one.p.rapidapi.com/v1/social/autolink"
HEADERS = {
    "content-type": "application/json",
    "X-RapidAPI-Key": RAPIDAPI_KEY,
    "X-RapidAPI-Host": "auto-download-all-in-one.p.rapidapi.com"
}

ADMINS = ["1879114908"]
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HTTP_URL_REGEXP = r'^(https?://[^\s]+)$'

PLATFORM_KEYWORDS = {
    "instagram.com": "Instagram",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "facebook.com": "Facebook",
    "tiktok.com": "TikTok"
}

PREFERRED_QUALITIES = ["hd_no_watermark", "no_watermark", "watermark"]

def get_platform_from_url(url: str) -> str:
    lower_url = url.lower()
    for keyword, platform_name in PLATFORM_KEYWORDS.items():
        if keyword in lower_url:
            return platform_name
    return "Unknown"

async def extract_audio_from_video(video_path: str) -> str:
    audio_path = video_path.replace(".mp4", ".mp3")
    try:
        subprocess.run(["ffmpeg", "-i", video_path, "-q:a", "0", "-map", "a", audio_path], check=True)
        return audio_path
    except subprocess.CalledProcessError as e:
        logger.error(f"Audio extraction failed: {e}")
        return None

async def recognize_song(audio_path: str) -> dict:
    try:
        shazam = Shazam()
        result = await shazam.recognize_song(audio_path)
        return result
    except Exception as e:
        logger.error(f"Error recognizing song: {e}")
        return None

@dp.message_handler(regexp=HTTP_URL_REGEXP)
async def handle_media_request(message: types.Message):
    try:
        logger.info(f"Received message from {message.from_user.id}: {message.text}")
        downloading_message = await message.reply("\ud83d\udce5 Yuklanmoqda, iltimos kuting...")

        platform = get_platform_from_url(message.text)
        cache_db.increment_request_count(platform)

        response_json = await fetch_media_info(message.text)
        medias = response_json.get('medias', [])

        if not medias:
            logger.warning("Media not found or URL is incorrect")
            await message.answer("\u26d4 Media topilmadi yoki URL noto'g'ri.")
        else:
            for media in medias:
                if media.get('type') == 'video':
                    await process_and_send_single_media(media, message, platform)
                else:
                    await message.answer("\u26a0 Faqat videolar uchun musiqa aniqlash mumkin.")

        await downloading_message.delete()
    except Exception as e:
        logger.error(f"Error handling media request: {e}")
        await message.answer("\u26a0 Xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")

async def fetch_media_info(url: str) -> dict:
    logger.info(f"Fetching media info for URL: {url}")
    try:
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

async def process_and_send_single_media(media: dict, message: types.Message, platform: str):
    downloaded_file = await get_cached_or_download(media)
    if not downloaded_file:
        logger.warning("Media could not be downloaded")
        await message.answer("\u274c Media yuklab olinmadi.")
        return

    # Audio extraction and song recognition
    audio_path = await extract_audio_from_video(downloaded_file)
    if audio_path:
        song_info = await recognize_song(audio_path)
        if song_info:
            track = song_info.get("track", "Noma'lum")
            artist = song_info.get("subtitle", "Noma'lum ijrochi")
            await message.answer(f"\ud83c\udfb6 Qo'shiq: {track}\n\ud83d\udc64 Ijrochi: {artist}")
        else:
            await message.answer("\u26a0 Qo'shiqni aniqlashda xatolik yuz berdi.")

    # Send the original video
    await send_media(downloaded_file, "video", message, platform, media.get('url'))
    if os.path.exists(downloaded_file):
        os.unlink(downloaded_file)

async def get_cached_or_download(media: dict) -> str:
    media_url = media.get('url')
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(media_url)
            if response.status_code == 200:
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_file:
                    tmp_file.write(response.content)
                    logger.info("Media downloaded successfully")
                    return tmp_file.name
            else:
                logger.warning(f"Failed to download media, status code: {response.status_code}")
                return ""
    except Exception as e:
        logger.error(f"Error downloading media: {e}")
        return ""

async def send_media(file, media_format, message: types.Message, platform: str, media_url: str):
    caption = "\u2728 @tinchrobot \u2013 <b>Tinchlikni xohlovchilar uchun!</b>\n"
    input_content = InputFile(file) if os.path.exists(str(file)) else file

    if media_format == "video":
        await message.answer_video(input_content, caption=caption, parse_mode="HTML")
