import logging
import httpx
import os
import tempfile
from aiogram import types
from aiogram.types import InputFile, MediaGroup
from loader import dp, bot, cache_db

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


def get_platform_from_url(url: str) -> str:
    lower_url = url.lower()
    for keyword, platform_name in PLATFORM_KEYWORDS.items():
        if keyword in lower_url:
            return platform_name
    return "Unknown"


@dp.message_handler(regexp=HTTP_URL_REGEXP)
async def handle_media_request(message: types.Message):
    try:
        logger.info(f"Received message from {message.from_user.id}: {message.text}")
        downloading_message = await message.reply("📥 Yuklanmoqda, iltimos kuting...")

        url = message.text.strip()
        platform = get_platform_from_url(url)
        cache_db.increment_request_count(platform)

        # Avval DB ni tekshiramiz: agar shu URL bo'yicha kesh bo'lsa to'g'ridan-to'g'ri yuboramiz
        cached_medias = cache_db.get_file_ids_by_url(url)
        if cached_medias:
            await send_cached_medias(message, cached_medias)
            await downloading_message.delete()
            return

        # Agar keshda bo'lmasa, API dan o'chiramiz
        response_json = await fetch_media_info(url)
        medias = response_json.get('medias', [])

        if not medias:
            logger.warning("Media not found or URL is incorrect")
            await message.answer("⛔ Media topilmadi yoki URL noto'g'ri.")
        else:
            # Bir nechta media bo'lsa hammasini yuklab, bitta MediaGroup da yuboramiz
            sent_file_ids = await process_and_send_all_medias(medias, message, platform, url)
            if sent_file_ids:
                logger.info("All medias sent and cached successfully.")
        await downloading_message.delete()
    except Exception as e:
        logger.error(f"Error handling media request: {e}")
        await message.answer("⚠️ Xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")


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


async def process_and_send_all_medias(medias: list, message: types.Message, platform: str, url: str):
    """
    Bir nechta media bo'lsa, ularni yuklab, MediaGroup bilan yuborish va DB ga file_id ni kiritish.
    """
    media_group = MediaGroup()
    downloaded_files = []
    media_types = []

    for media in medias:
        media_type = media.get('type')
        # Faqat video, rasm va audio turdagi media bilan ishlaymiz
        if media_type not in ['video', 'image', 'audio']:
            continue

        file_path = await get_cached_or_download(media_type, media.get('url'))
        if file_path:
            input_file = InputFile(file_path)
            # Media turiga qarab MediaGroup ga qo'shamiz
            if media_type == "video":
                media_group.attach_video(input_file)
            elif media_type == "image":
                media_group.attach_photo(input_file)
            elif media_type == "audio":
                media_group.attach_audio(input_file)
            downloaded_files.append(file_path)
            media_types.append(media_type)

    if not downloaded_files:
        # Hech narsa yuklanmadi
        await message.answer("❌ Media yuklab olinmadi yoki qo'llab-quvvatlanmaydi.")
        return []

    caption = "✨ @tinchrobot – <b>Tinchlikni xohlovchilar uchun!</b>"
    sent_messages = await message.answer_media_group(media_group)

    # Yuborilgan xabarlardan file_id ni olish va DB ga yozish
    sent_file_ids = []
    for msg, m_type, original_file in zip(sent_messages, media_types, downloaded_files):
        file_id = None
        if m_type == "video" and msg.video:
            file_id = msg.video.file_id
        elif m_type == "image" and msg.photo:
            file_id = msg.photo[-1].file_id
        elif m_type == "audio" and msg.audio:
            file_id = msg.audio.file_id

        if file_id:
            cache_db.add_cache(platform, url, file_id, m_type)
            sent_file_ids.append(file_id)

        if os.path.exists(original_file):
            os.unlink(original_file)


    return sent_file_ids


async def get_cached_or_download(media_type: str, media_url: str) -> str:
    extension = ".mp4"
    if media_type == "image":
        extension = ".jpg"
    elif media_type == "audio":
        extension = ".mp3"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(media_url)
            if response.status_code == 200:
                with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as tmp_file:
                    tmp_file.write(response.content)
                    logger.info("Media downloaded successfully")
                    return tmp_file.name
            else:
                logger.warning(f"Failed to download media, status code: {response.status_code}")
                return ""
    except Exception as e:
        logger.error(f"Error downloading media: {e}")
        return ""


async def send_cached_medias(message: types.Message, cached_medias: list):
    """
    Keshdan olingan media fayllar (file_id, media_type) shaklida keladi.
    Agar bitta media bo'lsa, shunchaki yuboramiz.
    Agar bir nechta bo'lsa, MediaGroup qilib yuboramiz.
    """
    caption = "✨ @tinchrobot – <b>Tinchlikni xohlovchilar uchun!</b>"
    if len(cached_medias) == 1:
        file_id, m_type = cached_medias[0]
        if m_type == "video":
            await message.answer_video(file_id, caption=caption, parse_mode="HTML")
        elif m_type == "image":
            await message.answer_photo(file_id, caption=caption, parse_mode="HTML")
        elif m_type == "audio":
            await message.answer_audio(file_id, caption=caption, parse_mode="HTML")
    else:
        media_group = MediaGroup()
        first = True
        for file_id, m_type in cached_medias:
            if m_type == "video":
                if first:
                    media_group.attach_video(file_id, caption=caption, parse_mode="HTML")
                    first = False
                else:
                    media_group.attach_video(file_id)
            elif m_type == "image":
                if first:
                    media_group.attach_photo(file_id, caption=caption, parse_mode="HTML")
                    first = False
                else:
                    media_group.attach_photo(file_id)
            elif m_type == "audio":
                if first:
                    media_group.attach_audio(file_id, caption=caption, parse_mode="HTML")
                    first = False
                else:
                    media_group.attach_audio(file_id)
        await message.answer_media_group(media_group)
