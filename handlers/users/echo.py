import logging
import httpx
import os
import tempfile
from aiogram import types
from aiogram.types import InputFile, MediaGroup, InlineKeyboardMarkup, InlineKeyboardButton
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

        response_json = await fetch_media_info(url)
        medias = response_json.get('medias', [])

        if not medias:
            logger.warning("Media not found or URL is incorrect")
            await message.answer("⛔ Media topilmadi yoki URL noto'g'ri.")
            await downloading_message.delete()
            return

        await handle_all_platforms(message, url, platform, medias)

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

async def handle_all_platforms(message: types.Message, url: str, platform: str, medias: list):
    """
    Barcha platformalar uchun yagona tartib:
    - Agar video bo'lsa, birinchi videoni tanlaydi.
      * Agar video >50MB bo'lsa, linkni yuboradi.
      * Agar audio ham bo'lsa, video yuborib inline tugma orqali audio yuklash imkoniyatini beradi.
    - Agar video bo'lmasa:
      * Bir nechta rasm bo'lsa, MediaGroup yuboradi.
      * Bitta rasm bo'lsa, oddiy yuboradi.
      * Agar faqat audio bo'lsa, audioni yuboradi.
      * Aks holda, media topilmadi xabarini yuboradi.
    """
    video_medias = [m for m in medias if m.get('type') == 'video']
    image_medias = [m for m in medias if m.get('type') == 'image']
    audio_medias = [m for m in medias if m.get('type') == 'audio']

    # Caption
    caption = "✨ @tinchrobot – <b>Tinchlikni xohlovchilar uchun!</b>"

    # Avval videoni tekshiramiz
    if video_medias:
        chosen_video = video_medias[0]
        file_path = await get_cached_or_download('video', chosen_video.get('url'))
        if not file_path:
            await message.answer("❌ Videoni yuklab bo'lmadi.")
            return
        file_size = os.path.getsize(file_path)
        if file_size > 50 * 1024 * 1024:
            # Video judayam katta, link yuboramiz
            if os.path.exists(file_path):
                os.unlink(file_path)
            await message.answer(f"Video hajmi juda katta (>50MB)\nLink: {chosen_video.get('url')}")
            return
        # Video kichik
        input_file = InputFile(file_path)
        # Agar audio bo'lsa, inline button
        if audio_medias:
            audio_url = audio_medias[0].get('url')
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton("🎵 Audio yuklab olish", callback_data=f"download_audio|{audio_url}|{platform}|{url}"))
            sent_msg = await message.answer_video(input_file, caption=caption, parse_mode="HTML", reply_markup=keyboard)
        else:
            sent_msg = await message.answer_video(input_file, caption=caption, parse_mode="HTML")

        # DB ga yozish
        if sent_msg.video:
            cache_db.add_cache(platform, url, sent_msg.video.file_id, 'video')
        if os.path.exists(file_path):
            os.unlink(file_path)
        return

    # Agar video bo'lmasa
    # Agar bir nechta rasm bo'lsa, MediaGroup
    if len(image_medias) > 1:
        await send_images_group(message, image_medias, platform, url)
        return
    elif len(image_medias) == 1:
        # Bitta rasm
        await send_single_media(message, image_medias[0], platform, url)
        return
    elif audio_medias:
        # Faqat audio bo'lsa
        await send_single_media(message, audio_medias[0], platform, url)
        return
    else:
        # Hech narsa yo'q
        await message.answer("⛔ Ushbu URL da yuklab bo'ladigan media topilmadi.")

async def send_images_group(message: types.Message, image_medias: list, platform: str, url: str):
    """
    Bir nechta rasmlarni MediaGroup sifatida yuborish.
    """
    if not image_medias:
        return
    media_group = MediaGroup()
    caption = "✨ @tinchrobot – <b>Tinchlikni xohlovchilar uchun!</b>"
    first = True
    downloaded_files = []
    for img in image_medias:
        file_path = await get_cached_or_download('image', img.get('url'))
        if not file_path:
            continue
        input_file = InputFile(file_path)
        if first:
            media_group.attach_photo(input_file, caption=caption, parse_mode="HTML")
            first = False
        else:
            media_group.attach_photo(input_file)
        downloaded_files.append((file_path, 'image'))

    if downloaded_files:
        sent_messages = await message.answer_media_group(media_group)
        # DB ga yozish
        for msg, (fpath, m_type) in zip(sent_messages, downloaded_files):
            if msg.photo:
                file_id = msg.photo[-1].file_id
                cache_db.add_cache(platform, url, file_id, m_type)
            if os.path.exists(fpath):
                os.unlink(fpath)

async def send_single_media(message: types.Message, media: dict, platform: str, url: str):
    """
    Bitta media (video/image/audio) ni yuborish.
    Video bo'lsa hajmini tekshir.
    Rasm va audio cheklovsiz yuboriladi.
    """
    m_type = media.get('type')
    m_url = media.get('url')
    caption = "✨ @tinchrobot – <b>Tinchlikni xohlovchilar uchun!</b>"

    if m_type == 'image':
        file_path = await get_cached_or_download('image', m_url)
        if file_path:
            sent_msg = await message.answer_photo(InputFile(file_path), caption=caption, parse_mode="HTML")
            if sent_msg.photo:
                cache_db.add_cache(platform, url, sent_msg.photo[-1].file_id, 'image')
            if os.path.exists(file_path):
                os.unlink(file_path)
    elif m_type == 'audio':
        file_path = await get_cached_or_download('audio', m_url)
        if file_path:
            sent_msg = await message.answer_audio(InputFile(file_path), caption=caption, parse_mode="HTML")
            if sent_msg.audio:
                cache_db.add_cache(platform, url, sent_msg.audio.file_id, 'audio')
            if os.path.exists(file_path):
                os.unlink(file_path)
    elif m_type == 'video':
        file_path = await get_cached_or_download('video', m_url)
        if file_path:
            file_size = os.path.getsize(file_path)
            if file_size > 50*1024*1024:
                # Link yuborish
                if os.path.exists(file_path):
                    os.unlink(file_path)
                await message.answer(f"Video hajmi juda katta (>50MB)\nLink: {m_url}")
            else:
                sent_msg = await message.answer_video(InputFile(file_path), caption=caption, parse_mode="HTML")
                if sent_msg.video:
                    cache_db.add_cache(platform, url, sent_msg.video.file_id, 'video')
                if os.path.exists(file_path):
                    os.unlink(file_path)

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

@dp.callback_query_handler(lambda c: c.data.startswith("download_audio|"))
async def download_audio_callback_handler(callback_query: types.CallbackQuery):
    """
    Tugma bosilganda audio yuklab yuborish
    callback_data: "download_audio|<audio_url>|<platform>|<url>"
    """
    data = callback_query.data.split("|")
    if len(data) < 4:
        await callback_query.answer("Xatolik yuz berdi")
        return
    _, audio_url, platform, orig_url = data

    await callback_query.answer("Yuklab berayapman, iltimos kuting...")

    file_path = await get_cached_or_download('audio', audio_url)
    caption = "✨ @tinchrobot – <b>Tinchlikni xohlovchilar uchun!</b>"
    if file_path:
        sent_msg = await bot.send_audio(chat_id=callback_query.message.chat.id, audio=InputFile(file_path), caption=caption, parse_mode="HTML")
        if sent_msg.audio:
            cache_db.add_cache(platform, orig_url, sent_msg.audio.file_id, 'audio')
        if os.path.exists(file_path):
            os.unlink(file_path)
    else:
        await bot.send_message(callback_query.message.chat.id, "❌ Audioni yuklab bo'lmadi.")
