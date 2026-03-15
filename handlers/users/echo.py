import asyncio
import logging
import os
from aiogram import types
from aiogram.types import InputFile, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
from aiogram.utils.markdown import html_decoration as hd
from loader import dp, bot, cache_db
from utils.video_downloader import (
    download_video, cleanup_file, is_supported_url, get_platform_from_url,
    get_youtube_formats, download_youtube_with_format, make_url_hash, get_cached_yt_url,
)
from utils.pyrogram_client import send_large_video
from utils import pyrogram_client as _pyro_mod
from keyboards.inline.quality_kb import youtube_quality_keyboard

FILE_SIZE_LIMIT = 50 * 1024 * 1024  # 50MB (aiogram limiti)
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB (pyrogram limiti)

# Video file_id larni vaqtincha saqlash (musiqani yuklash tugmasi uchun)
_video_file_ids = {}

logger = logging.getLogger(__name__)

HTTP_URL_REGEXP = r'^(https?://[^\s]+)$'


def _is_youtube_url(url: str) -> bool:
    lower = url.lower()
    return any(k in lower for k in ("youtube.com", "youtu.be", "music.youtube.com"))


@dp.message_handler(regexp=HTTP_URL_REGEXP)
async def handle_media_request(message: types.Message):
    url = message.text.strip()

    if not is_supported_url(url):
        await message.reply(
            "⚠️ Bu platforma hozircha qo'llab-quvvatlanmaydi.\n"
            "Quyidagi platformalar ishlaydi: Instagram, TikTok, YouTube, "
            "Facebook, Twitter/X, Vimeo, Reddit, Pinterest"
        )
        return

    platform = get_platform_from_url(url)
    user_id = message.from_user.id
    logger.info(f"User: {user_id}, URL: {url}, Platform: {platform}")

    try:
        await cache_db.increment_request_count(platform)
    except Exception as e:
        logger.error(f"Statistika xatosi: {e}")

    # Cache tekshirish
    cached = await cache_db.get_file_id_by_url(url)
    if cached:
        await send_cached_media(message, cached["file_id"], cached["media_type"])
        return

    # YouTube — format tanlash
    if _is_youtube_url(url):
        await _handle_youtube_quality_selection(message, url, platform)
        return

    # Boshqa platformalar
    await _download_and_send(message, url, platform)


async def _handle_youtube_quality_selection(message: types.Message, url: str, platform: str):
    loading_msg = await message.reply("🔍 YouTube video formatlarini tekshirilmoqda...")

    try:
        formats_data = await get_youtube_formats(url)

        if not formats_data or not formats_data.get("formats"):
            await loading_msg.edit_text(f"📥 {platform} dan yuklanmoqda...")
            await _download_and_send(message, url, platform, loading_msg)
            return

        title = formats_data.get("title", "YouTube Video")
        url_hash = formats_data["url_hash"]
        formats = formats_data["formats"]
        duration = formats_data.get("duration", 0)

        dur_text = ""
        if duration:
            mins, secs = divmod(int(duration), 60)
            hours, mins = divmod(mins, 60)
            if hours:
                dur_text = f"⏱ {hours}:{mins:02d}:{secs:02d}"
            else:
                dur_text = f"⏱ {mins}:{secs:02d}"

        kb = youtube_quality_keyboard(url_hash, formats)
        text = f"🎬 <b>{title}</b>\n{dur_text}\n\n📐 Sifatni tanlang:"
        await loading_msg.edit_text(text, reply_markup=kb, parse_mode="HTML")

    except Exception as e:
        logger.error(f"YouTube format tanlash xatosi: {e}", exc_info=True)
        await loading_msg.edit_text(f"📥 {platform} dan yuklanmoqda...")
        await _download_and_send(message, url, platform, loading_msg)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("vidmusic:"))
async def handle_video_music_callback(callback: types.CallbackQuery):
    """Video dan musiqani aniqlash va yuklash"""
    short_key = callback.data.split(":", 1)[1]
    vid_file_id = _video_file_ids.pop(short_key, None)

    if not vid_file_id:
        await callback.answer("Ma'lumot eskirgan. Videoni qayta yuboring.")
        return

    await callback.answer("Aniqlanmoqda...")

    # Tugmani o'chirish
    try:
        await callback.message.delete()
    except Exception:
        pass

    chat_id = callback.message.chat.id
    asyncio.create_task(_shazam_from_video(chat_id, vid_file_id))


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("ytq:"))
async def handle_youtube_quality_callback(callback: types.CallbackQuery):
    await callback.answer()

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.message.edit_text("⚠️ Noto'g'ri format.")
        return

    _, url_hash, format_id = parts
    url = get_cached_yt_url(url_hash)
    if not url:
        await callback.message.edit_text("⏳ Havola muddati tugagan. Qayta yuboring.")
        return

    platform = "YouTube"
    cached = await cache_db.get_file_id_by_url(url)
    if cached and format_id != "audio":
        try:
            await callback.message.delete()
        except Exception:
            pass
        await send_cached_media_to_chat(
            callback.message.chat.id, cached["file_id"], cached["media_type"]
        )
        return

    if format_id == "audio":
        await callback.message.edit_text("🎵 Audio yuklanmoqda...")
    else:
        await callback.message.edit_text(f"📥 YouTube dan yuklanmoqda...")

    downloading_msg = callback.message
    file_path = None

    try:
        result = await download_youtube_with_format(url_hash, format_id)
        if not result or not result.get('file_path'):
            result = await download_video(url)
        if not result or not result.get('file_path'):
            await downloading_msg.edit_text("⛔ Yuklab olib bo'lmadi. Qayta urinib ko'ring.")
            return

        file_path = result['file_path']
        is_audio = result.get('is_audio', False)
        await _send_result(downloading_msg, result, url, platform, is_audio=is_audio)

    except Exception as e:
        logger.error(f"YouTube quality download xatosi: {e}", exc_info=True)
        try:
            await downloading_msg.edit_text("⚠️ Xatolik yuz berdi. Qayta urinib ko'ring.")
        except Exception:
            pass
    finally:
        if file_path:
            cleanup_file(file_path)


async def _download_and_send(
    message: types.Message, url: str, platform: str,
    downloading_message: types.Message = None
):
    if not downloading_message:
        downloading_message = await message.reply(f"📥 {platform} dan yuklanmoqda...")

    file_path = None
    file_paths = []
    try:
        result = await download_video(url)

        if not result:
            await downloading_message.edit_text(
                "⛔ Media topilmadi yoki yuklab olishda xatolik yuz berdi.\n"
                "Iltimos, havolani tekshirib qayta urinib ko'ring."
            )
            return

        # Carousel / ko'p medialar
        if 'media_list' in result:
            media_list = result['media_list']
            file_paths = [item['file_path'] for item in media_list]
            await _send_media_group(downloading_message, media_list, url, platform)
            return

        if not result.get('file_path'):
            await downloading_message.edit_text(
                "⛔ Media topilmadi yoki yuklab olishda xatolik yuz berdi.\n"
                "Iltimos, havolani tekshirib qayta urinib ko'ring."
            )
            return

        file_path = result['file_path']
        is_audio = result.get('is_audio', False)
        await _send_result(downloading_message, result, url, platform, is_audio=is_audio)

    except Exception as e:
        logger.error(f"Video yuklab olish xatosi: {e}", exc_info=True)
        try:
            await downloading_message.edit_text("⚠️ Xatolik yuz berdi. Qayta urinib ko'ring.")
        except Exception:
            pass
    finally:
        if file_path:
            cleanup_file(file_path)
        for fp in file_paths:
            cleanup_file(fp)


async def _shazam_from_video(chat_id: int, file_id: str):
    """Tugma bosilganda: video yuklab -> Shazam -> musiqa yuklash"""
    import tempfile as _tf
    import shutil as _shutil
    from handlers.users.music_search import (
        extract_audio_from_video, recognize_audio_shazam,
        download_and_send_audio, search_music_youtube,
    )

    status_msg = await bot.send_message(chat_id, "🎵 Musiqa aniqlanmoqda...")
    tmp_dir = _tf.mkdtemp(prefix="shazam_vid_")

    try:
        # Video ni yuklab olish (20MB gacha bot API, kattaroq pyrogram)
        video_path = os.path.join(tmp_dir, "video.mp4")
        try:
            file_info = await bot.get_file(file_id)
            await bot.download_file(file_info.file_path, destination=video_path)
        except Exception:
            if _pyro_mod.pyro_client:
                await _pyro_mod.pyro_client.download_media(file_id, file_name=video_path)
            else:
                await status_msg.edit_text("⚠️ Video juda katta, yuklab bo'lmadi.")
                return

        # Audio ajratish
        audio_path = await extract_audio_from_video(video_path)
        try:
            os.remove(video_path)
        except Exception:
            pass
        if not audio_path:
            await status_msg.edit_text("😔 Videodan audio ajratib bo'lmadi.")
            return

        # Shazam
        result = await recognize_audio_shazam(audio_path)
        try:
            os.remove(audio_path)
        except Exception:
            pass

        if not result or not result.get('title'):
            await status_msg.edit_text("😔 Musiqa aniqlab bo'lmadi.")
            return

        artist = result['artist']
        title = result['title']
        await status_msg.edit_text(
            f"🎵 Topildi: {artist} - {title}\n⏳ Yuklanmoqda..."
        )

        # YouTube dan qidirish va yuklash
        search_q = f"{artist} {title}"
        yt_results = await search_music_youtube(search_q, max_results=3)
        if not yt_results:
            await status_msg.edit_text(
                f"🎵 {artist} - {title}\n⚠️ Yuklab bo'lmadi."
            )
            return

        success = await download_and_send_audio(
            chat_id, yt_results[0]['url'],
            title_hint=title, artist_hint=artist,
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
        if not success:
            await bot.send_message(
                chat_id, f"🎵 {artist} - {title}\n⚠️ Yuklab bo'lmadi."
            )

    except Exception as e:
        logger.error(f"Shazam from video xatosi: {e}", exc_info=True)
        try:
            await status_msg.edit_text("⚠️ Xatolik yuz berdi.")
        except Exception:
            pass
    finally:
        _shutil.rmtree(tmp_dir, ignore_errors=True)


async def _send_result(
    status_message: types.Message, result: dict, url: str,
    platform: str, is_audio: bool = False
):
    file_path = result['file_path']
    file_size = result['filesize']
    title = result.get('title', 'Video')
    duration = result.get('duration', 0)
    width = result.get('width', 0)
    height = result.get('height', 0)
    is_photo = result.get('is_photo', False)
    chat_id = status_message.chat.id

    caption = f"✨ {hd.bold('@tinchrobot')} – Tinchlikni xohlovchilar uchun!"

    if file_size > MAX_FILE_SIZE:
        await status_message.edit_text(
            f"⛔ Fayl hajmi juda katta (>{MAX_FILE_SIZE // (1024*1024*1024)}GB)."
        )
        return

    # Rasm yuborish
    if is_photo:
        input_file = InputFile(file_path)
        sent_msg = await bot.send_photo(
            chat_id=chat_id, photo=input_file,
            caption=caption, parse_mode="HTML",
        )
        if sent_msg.photo:
            await cache_db.add_cache(platform, url, sent_msg.photo[-1].file_id, "photo")
        try:
            await status_message.delete()
        except Exception:
            pass
        return

    if is_audio:
        if file_size > FILE_SIZE_LIMIT and _pyro_mod.pyro_client:
            size_mb = file_size // (1024 * 1024)
            await status_message.edit_text(f"📤 Audio yuborilmoqda ({size_mb}MB)...")
            msg = await _pyro_mod.pyro_client.send_audio(
                chat_id=chat_id, audio=file_path,
                caption=caption, duration=duration, title=title,
            )
            if msg and msg.audio:
                await cache_db.add_cache(platform, url, msg.audio.file_id, "audio")
            try:
                await status_message.delete()
            except Exception:
                pass
        else:
            input_file = InputFile(file_path)
            sent_msg = await bot.send_audio(
                chat_id=chat_id, audio=input_file,
                caption=caption, parse_mode="HTML",
                duration=duration, title=title,
            )
            if sent_msg.audio:
                await cache_db.add_cache(platform, url, sent_msg.audio.file_id, "audio")
            try:
                await status_message.delete()
            except Exception:
                pass
        return

    # Video yuborish
    _sent_video_file_id = None
    if file_size > FILE_SIZE_LIMIT:
        if _pyro_mod.pyro_client:
            size_mb = file_size // (1024 * 1024)
            await status_message.edit_text(f"📤 Katta fayl yuborilmoqda ({size_mb}MB)...")
            file_id = await send_large_video(
                chat_id=chat_id, file_path=file_path,
                caption=caption, duration=duration,
                width=width, height=height,
            )
            if file_id:
                _sent_video_file_id = file_id
                await cache_db.add_cache(platform, url, file_id, "video")
                try:
                    await status_message.delete()
                except Exception:
                    pass
            else:
                await status_message.edit_text("⚠️ Katta faylni yuborishda xatolik.")
        else:
            await status_message.edit_text(
                f"📎 Video hajmi katta ({file_size // (1024*1024)}MB)."
            )
    else:
        input_file = InputFile(file_path)
        sent_msg = await bot.send_video(
            chat_id=chat_id, video=input_file,
            caption=caption, parse_mode="HTML",
            duration=duration, width=width, height=height,
            supports_streaming=True,
        )
        if sent_msg.video:
            _sent_video_file_id = sent_msg.video.file_id
            await cache_db.add_cache(platform, url, sent_msg.video.file_id, "video")
        try:
            await status_message.delete()
        except Exception:
            pass

    # Video yuborilgandan keyin "Musiqani yuklash" tugmasi
    if not is_audio and not is_photo and _sent_video_file_id:
        try:
            vid_file_id = _sent_video_file_id
            if vid_file_id:
                import hashlib as _hl
                short_key = _hl.md5(vid_file_id.encode()).hexdigest()[:12]
                _video_file_ids[short_key] = vid_file_id
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton(
                    text="🎵 Musiqani yuklash",
                    callback_data=f"vidmusic:{short_key}",
                ))
                await bot.send_message(
                    chat_id, "🎬 Videodagi musiqani yuklab olish:",
                    reply_markup=markup,
                )
        except Exception:
            pass


async def _send_media_group(
    status_message: types.Message, media_list: list, url: str, platform: str
):
    """Carousel / ko'p medialarni media group sifatida yuborish"""
    chat_id = status_message.chat.id
    caption = f"✨ {hd.bold('@tinchrobot')} – Tinchlikni xohlovchilar uchun!"

    # Telegram media group max 10 ta element
    items = media_list[:10]
    media_group = []

    for i, item in enumerate(items):
        fp = item['file_path']
        is_photo = item.get('is_photo', False)
        item_caption = caption if i == 0 else None

        if is_photo:
            media_group.append(InputMediaPhoto(
                media=InputFile(fp),
                caption=item_caption,
                parse_mode="HTML" if item_caption else None,
            ))
        else:
            media_group.append(InputMediaVideo(
                media=InputFile(fp),
                caption=item_caption,
                parse_mode="HTML" if item_caption else None,
                duration=item.get('duration', 0),
                width=item.get('width', 0),
                height=item.get('height', 0),
                supports_streaming=True,
            ))

    try:
        if media_group:
            await bot.send_media_group(chat_id=chat_id, media=media_group)
        try:
            await status_message.delete()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Media group yuborishda xatolik: {e}", exc_info=True)
        # Fallback: bitta-bitta yuborish
        for item in items:
            try:
                if item.get('is_photo'):
                    await bot.send_photo(chat_id, InputFile(item['file_path']),
                                         caption=caption, parse_mode="HTML")
                else:
                    await bot.send_video(chat_id, InputFile(item['file_path']),
                                         caption=caption, parse_mode="HTML",
                                         supports_streaming=True)
            except Exception as e2:
                logger.error(f"Fallback yuborishda xatolik: {e2}")
        try:
            await status_message.delete()
        except Exception:
            pass


async def send_cached_media(message: types.Message, file_id: str, media_type: str = "document"):
    caption = f"✨ {hd.bold('@tinchrobot')} – Tinchlikni xohlovchilar uchun!"
    try:
        if media_type == "video":
            await message.answer_video(file_id, caption=caption, parse_mode="HTML")
        elif media_type == "audio":
            await message.answer_audio(file_id, caption=caption, parse_mode="HTML")
        elif media_type == "photo":
            await message.answer_photo(file_id, caption=caption, parse_mode="HTML")
        else:
            await message.answer_document(file_id, caption=caption, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Cache dan yuborishda xatolik: {e}")
        await cache_db.delete_cache_by_url(message.text.strip())
        await message.reply("⚠️ Cache eskirgan. Iltimos, havolani qayta yuboring.")


async def send_cached_media_to_chat(chat_id: int, file_id: str, media_type: str = "document"):
    caption = f"✨ {hd.bold('@tinchrobot')} – Tinchlikni xohlovchilar uchun!"
    try:
        if media_type == "video":
            await bot.send_video(chat_id, file_id, caption=caption, parse_mode="HTML")
        elif media_type == "audio":
            await bot.send_audio(chat_id, file_id, caption=caption, parse_mode="HTML")
        elif media_type == "photo":
            await bot.send_photo(chat_id, file_id, caption=caption, parse_mode="HTML")
        else:
            await bot.send_document(chat_id, file_id, caption=caption, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Cache dan yuborishda xatolik: {e}")
