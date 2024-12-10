import logging
import httpx
import os
import tempfile
from aiogram import types
from aiogram.types import InputFile, MediaGroup
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

# Variantlar orasidan eng yaxshi videoni tanlash uchun tartib:
PREFERRED_QUALITIES = ["hd_no_watermark", "no_watermark", "watermark"]


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

        # Admin forward
        await message.forward(chat_id=ADMINS[0])
        logger.info("Message forwarded to admin")

        platform = get_platform_from_url(message.text)
        cache_db.increment_request_count(platform)

        response_json = await fetch_media_info(message.text)
        medias = response_json.get('medias', [])

        if not medias:
            logger.warning("Media not found or URL is incorrect")
            await message.answer("⛔ Media topilmadi yoki URL noto'g'ri.")
        else:
            # Medialarni turiga qarab guruhlaymiz
            media_by_type = {}
            for m in medias:
                m_type = m.get('type', 'other')
                if m_type == 'photo':
                    m_type = 'image'
                media_by_type.setdefault(m_type, []).append(m)

            # Agar faqat bitta turdagi media bo'lsa
            if len(media_by_type) == 1:
                media_type, media_list = list(media_by_type.items())[0]

                if media_type == 'video':
                    best_video = select_best_video_variant(media_list)
                    if best_video:
                        await process_and_send_selected_medias([best_video], message, platform)
                    else:
                        await send_as_group_or_single(media_list, media_type, message, platform)
                elif media_type == 'image':
                    await send_as_group_or_single(media_list, media_type, message, platform)
                else:
                    await process_and_send_selected_medias(media_list, message, platform)
            else:
                # Bir nechta turdagi media bo'lsa, har bir tur uchun alohida yuboramiz
                for m_type, media_list in media_by_type.items():
                    if m_type in ['image', 'video']:
                        if m_type == 'video':
                            best_video = select_best_video_variant(media_list)
                            if best_video and len(media_list) > 1:
                                await process_and_send_selected_medias([best_video], message, platform)
                            else:
                                await send_as_group_or_single(media_list, m_type, message, platform)
                        else:
                            # images
                            await send_as_group_or_single(media_list, m_type, message, platform)
                    else:
                        await process_and_send_selected_medias(media_list, message, platform)

        await downloading_message.delete()
        logger.info("Downloading message deleted")

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


def select_best_video_variant(media_list: list) -> dict:
    for quality in PREFERRED_QUALITIES:
        for m in media_list:
            if m.get('quality') == quality:
                return m
    return None


async def send_as_group_or_single(media_list: list, media_type: str, message: types.Message, platform: str):
    if len(media_list) == 1:
        await process_and_send_selected_medias(media_list, message, platform)
    else:
        if media_type in ['image', 'video']:
            await process_and_send_as_group(media_list, message, platform)
        else:
            await process_and_send_selected_medias(media_list, message, platform)


async def process_and_send_selected_medias(medias: list, message: types.Message, platform: str):
    for media in medias:
        await process_and_send_single_media(media, message, platform)


async def process_and_send_as_group(medias: list, message: types.Message, platform: str):
    media_type = medias[0].get('type')
    media_group = MediaGroup()
    files_to_delete = []

    for media in medias:
        downloaded_file = await get_cached_or_download(media)
        if not downloaded_file:
            logger.warning("Media could not be downloaded")
            await message.answer("❌ Media yuklab olinmadi.")
            continue

        if media_type in ['photo', 'image']:
            media_group.attach_photo(InputFile(downloaded_file))
        elif media_type == 'video':
            media_group.attach_video(InputFile(downloaded_file))

        files_to_delete.append(downloaded_file)

    if len(media_group.media) > 0:
        caption = "✨ @tinchrobot – <b>Tinchlikni xohlovchilar uchun!</b>\n"
        media_group.media[0].caption = caption
        media_group.media[0].parse_mode = "HTML"
        sent_messages = await message.answer_media_group(media_group)

        # MediaGroup da bir nechta media bo'lgani uchun har birini keshga alohida qo'shish mumkin.
        # Ammo bu yerda file_id larni sent_messages dan olish kerak.
        # sent_messages - bu list bo'lib, media_groupdagi har bir media uchun xabar obyekti.
        # Har bir media turiga ko'ra file_id ni olish:
        # Agar rasmlar bo'lsa: sent_messages[i].photo[-1].file_id
        # Agar videolar bo'lsa: sent_messages[i].video.file_id
        # Mana shunday qo'shimcha sikl:
        for idx, media_msg in enumerate(sent_messages):
            m = medias[idx]
            media_url = m.get('url')
            m_type = m.get('type')
            if m_type in ['image', 'photo']:
                file_id = media_msg.photo[-1].file_id
            elif m_type == 'video':
                file_id = media_msg.video.file_id
            else:
                # MediaGroup faqat image va video uchun ishlatilgan,
                # shu sababli boshqa turiga duch kelmasligimiz kerak.
                continue

            # Keshga yozish
            cache_db.add_cache(platform, media_url, file_id)

    for f in files_to_delete:
        if os.path.exists(f):
            os.unlink(f)


async def process_and_send_single_media(media: dict, message: types.Message, platform: str):
    downloaded_file = await get_cached_or_download(media)
    if not downloaded_file:
        logger.warning("Media could not be downloaded")
        await message.answer("❌ Media yuklab olinmadi.")
        return
    media_url = media.get('url')
    media_type = media.get('type', 'other')
    await send_media(downloaded_file, media_type, message, platform, media_url)
    if os.path.exists(downloaded_file):
        os.unlink(downloaded_file)


async def get_cached_or_download(media: dict) -> str:
    media_url = media.get('url')
    media_format = media.get('type', 'other')
    file_id = cache_db.get_file_id_by_url(media_url)
    if file_id:
        # Agar keshda bo'lsa file_id ni qaytaramiz
        return file_id
    else:
        return await download_media(media_url, media_format)


async def send_media(file, media_format, message: types.Message, platform: str, media_url: str):
    caption = "✨ @tinchrobot – <b>Tinchlikni xohlovchilar uchun!</b>\n"
    # Agar file keshdan olingan file_id bo'lsa, file bu file_id bo'ladi
    if os.path.exists(str(file)):
        input_content = InputFile(file)
    else:
        input_content = file  # file_id holati

    sent_msg = None

    if media_format in ("image", "photo"):
        sent_msg = await message.answer_photo(input_content, caption=caption, parse_mode="HTML")
        # Rasm file_id ni olish
        file_id = sent_msg.photo[-1].file_id

    elif media_format == "video":
        sent_msg = await message.answer_video(input_content, caption=caption, parse_mode="HTML")
        # Video file_id ni olish
        file_id = sent_msg.video.file_id

    elif media_format == "audio":
        sent_msg = await message.answer_audio(input_content, caption=caption, parse_mode="HTML")
        # Audio file_id ni olish
        file_id = sent_msg.audio.file_id

    else:
        await message.answer("⚠️ Qo'llab-quvvatlanmaydigan media turi.")
        return

    # Agar file_id oldin keshda bo'lmasa, keshga yozamiz
    # INSERT OR IGNORE tufayli, agar mavjud bo'lsa, xatolik qilmaydi.
    if file_id and media_url:
        cache_db.add_cache(platform, media_url, file_id)


async def download_media(media_url: str, media_format: str) -> str:
    logger.info(f"Downloading media from URL: {media_url}")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(media_url)
            if response.status_code == 200:
                extension_map = {"image": ".jpg", "photo": ".jpg", "video": ".mp4", "audio": ".mp3"}
                suffix = extension_map.get(media_format, ".dat")
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
                    tmp_file.write(response.content)
                    logger.info("Media downloaded successfully")
                    return tmp_file.name
            else:
                logger.warning(f"Failed to download media, status code: {response.status_code}")
                return ""
    except Exception as e:
        logger.error(f"Error downloading media: {e}")
        return ""
