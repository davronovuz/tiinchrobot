import asyncio
import logging
import os
from aiogram import types
from aiogram.types import InputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.markdown import html_decoration as hd
from loader import dp, bot, cache_db
from utils.video_downloader import (
    download_video, cleanup_file, is_supported_url, get_platform_from_url,
    get_youtube_formats, download_youtube_with_format, make_url_hash, get_cached_yt_url,
)
from utils.pyrogram_client import send_large_video, pyro_client
from keyboards.inline.quality_kb import youtube_quality_keyboard

FILE_SIZE_LIMIT = 50 * 1024 * 1024  # 50MB (aiogram limiti)
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB (pyrogram limiti)

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
    try:
        result = await download_video(url)

        if not result or not result.get('file_path'):
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


async def _auto_shazam_video(chat_id: int, file_path: str):
    """Video fayldan avtomatik musiqa aniqlash (background). file_path = nusxalangan fayl"""
    import shutil as _shutil
    tmp_dir = os.path.dirname(file_path)
    try:
        from handlers.users.music_search import (
            extract_audio_from_video, recognize_audio_shazam,
            search_music_youtube, user_results,
        )

        if not os.path.exists(file_path):
            return

        audio_path = await extract_audio_from_video(file_path)
        # Video nusxani tozalash
        try:
            os.remove(file_path)
        except Exception:
            pass
        if not audio_path:
            return

        result = await recognize_audio_shazam(audio_path)

        # Audio tozalash
        try:
            os.remove(audio_path)
        except Exception:
            pass

        if not result or not result.get('title'):
            return

        # Natija matni
        text_parts = [
            "🎵 Videodagi musiqa aniqlandi!\n",
            f"🎤 Ijrochi: {result['artist']}",
            f"🎶 Nomi: {result['title']}",
        ]
        if result.get('album'):
            text_parts.append(f"💿 Albom: {result['album']}")
        if result.get('genre'):
            text_parts.append(f"🏷 Janr: {result['genre']}")

        text = "\n".join(text_parts)

        # YouTube dan birinchi natijani topish
        search_query = f"{result['artist']} {result['title']}"
        yt_results = await search_music_youtube(search_query, max_results=3)

        if yt_results:
            # Yuklash tugmasi
            shazam_key = f"shazam_dl_{chat_id}"
            user_results[shazam_key] = {
                'url': yt_results[0]['url'],
                'title': result['title'],
                'artist': result['artist'],
            }

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(
                text="🎵 Yuklash",
                callback_data=f"shazam_dl:{chat_id}",
            ))

            if result.get('cover_url'):
                try:
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=result['cover_url'],
                        caption=text,
                        reply_markup=markup,
                    )
                    return
                except Exception:
                    pass

            await bot.send_message(chat_id, text, reply_markup=markup)
        else:
            # YouTube da topilmasa faqat info ko'rsatish
            if result.get('cover_url'):
                try:
                    await bot.send_photo(chat_id=chat_id, photo=result['cover_url'], caption=text)
                    return
                except Exception:
                    pass
            await bot.send_message(chat_id, text)

    except Exception as e:
        logger.error(f"Auto Shazam xatosi: {e}", exc_info=True)
    finally:
        try:
            _shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


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
        if file_size > FILE_SIZE_LIMIT and pyro_client:
            size_mb = file_size // (1024 * 1024)
            await status_message.edit_text(f"📤 Audio yuborilmoqda ({size_mb}MB)...")
            msg = await pyro_client.send_audio(
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
    if file_size > FILE_SIZE_LIMIT:
        if pyro_client:
            size_mb = file_size // (1024 * 1024)
            await status_message.edit_text(f"📤 Katta fayl yuborilmoqda ({size_mb}MB)...")
            file_id = await send_large_video(
                chat_id=chat_id, file_path=file_path,
                caption=caption, duration=duration,
                width=width, height=height,
            )
            if file_id:
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
            await cache_db.add_cache(platform, url, sent_msg.video.file_id, "video")
        try:
            await status_message.delete()
        except Exception:
            pass

    # Video yuborilgandan keyin avtomatik Shazam (background)
    # Faylni nusxalash kerak — asl fayl tez o'chiriladi
    if not is_audio and not is_photo and os.path.exists(file_path):
        import tempfile as _tf
        import shutil as _sh
        tmp_dir = _tf.mkdtemp(prefix="auto_shazam_copy_")
        _, ext = os.path.splitext(file_path)
        copy_path = os.path.join(tmp_dir, f"video{ext}")
        try:
            _sh.copy2(file_path, copy_path)
            asyncio.create_task(_auto_shazam_video(chat_id, copy_path))
        except Exception:
            _sh.rmtree(tmp_dir, ignore_errors=True)


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
