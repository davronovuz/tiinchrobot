import asyncio
import os
import tempfile
import shutil
import logging
from io import BytesIO

import httpx
from aiogram import types
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ContentType,
)

from loader import dp, bot, cache_db
from keyboards.default.menu_i import world_track, top_track, main_btn
from utils.misc.download_file import world_music, main_data, top_music, new_trek

logger = logging.getLogger(__name__)

BGUTIL_URL = os.getenv("BGUTIL_URL", "http://bgutil:4416")
WARP_PROXY = "socks5://warp:9091"


# =====================================================
# /tiktok, /top, /new komandalari
# =====================================================

@dp.message_handler(commands='tiktok')
async def tik_tok_handler(msg: types.Message):
    text = 'Siz uchun top 10 Tik-Tok Musiqalar!\n\n'
    sana = 1
    for i in world_music():
        text += f"{str(sana)}. {i['artist']} - {i['title']}\n"
        sana += 1
    await msg.answer(text=text, reply_markup=world_track())


@dp.callback_query_handler(lambda x: x.data in [i['id'] for i in world_music()])
async def tik_tok_callback(callback: types.CallbackQuery):
    user_id = callback.data
    for i in world_music():
        if i['id'] == user_id:
            await callback.message.answer_audio(i['track'], f"{i['artist']} - {i['title']}")


@dp.message_handler(commands='top')
async def top_handler(msg: types.Message):
    text = 'Siz uchun top 10 Musiqalar!\n\n'
    sana = 1
    for i in top_music():
        text += f"{str(sana)}. {i['artist']} - {i['title']}\n"
        sana += 1
    await msg.answer(text=text, reply_markup=top_track())


@dp.callback_query_handler(lambda msg: msg.data in [i['id'] for i in top_music()])
async def welcome(callback: types.CallbackQuery):
    region_id = callback.data
    for i in top_music():
        if i['id'] == region_id:
            await callback.message.answer_audio(i['track'], f"{i['artist']} - {i['title']}")


@dp.message_handler(commands='new')
async def new_music_handler(msg: types.Message):
    text = 'Siz uchun 10 yangi Musiqalar!\n\n'
    sana = 1
    for i in new_trek():
        text += f"{str(sana)}. {i['artist']} - {i['title']}\n"
        sana += 1
    await msg.answer(text=text, reply_markup=main_btn())


@dp.callback_query_handler(lambda x: x.data in [i['id'] for i in new_trek()])
async def new_callback_handler(callback: types.CallbackQuery):
    data_id = callback.data
    for i in new_trek():
        if data_id == i['id']:
            await callback.message.answer_audio(i['track'], f"{i['artist']} - {i['title']}")


@dp.callback_query_handler(lambda msg: msg.data == 'remove')
async def remove(callback: types.CallbackQuery):
    await callback.message.delete()


# =====================================================
# Foydalanuvchi qidiruv natijalarini saqlash
# =====================================================
user_results = {}


# =====================================================
# YouTube Music qidiruv
# =====================================================

def _yt_base_opts(use_proxy=False):
    """YouTube uchun umumiy opsiyalar — android_vr + bgutil"""
    opts = {
        'extractor_args': {
            'youtube': {
                'player_client': ['android_vr'],
                'player_skip': [],
            },
            'youtubepot-bgutilhttp': {
                'base_url': [BGUTIL_URL],
            },
        },
    }
    if use_proxy:
        opts['proxy'] = WARP_PROXY
    return opts


def _get_ydl_opts_search(max_results=20):
    """yt-dlp qidiruv uchun sozlamalar (proxysiz — tez)"""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'default_search': f'ytsearch{max_results}',
        'socket_timeout': 10,
    }
    opts.update(_yt_base_opts(use_proxy=False))
    return opts


def _get_ydl_opts_download(tmp_dir, use_proxy=False):
    """yt-dlp yuklash uchun sozlamalar — m4a to'g'ridan-to'g'ri (konvertatsiyasiz)"""
    opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'outtmpl': os.path.join(tmp_dir, '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 3,
        'fragment_retries': 3,
        'extractor_retries': 2,
    }
    opts.update(_yt_base_opts(use_proxy=use_proxy))
    return opts


async def search_music_deezer(query, max_results=20):
    """Deezer API dan musiqa qidirish — juda tez (<1 sek)"""
    results = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.deezer.com/search",
                params={"q": query, "limit": max_results, "order": "RANKING"},
            )
            if resp.status_code != 200:
                return results

            data = resp.json()
            for track in data.get("data", []):
                title = track.get("title", "")
                artist = track.get("artist", {}).get("name", "")
                duration = track.get("duration", 0)
                track_id = track.get("id", 0)
                if not title:
                    continue
                results.append({
                    "title": title,
                    "artist": artist,
                    "url": f"deezer:{track_id}",
                    "source": "deezer",
                    "type": "deezer",
                    "duration": int(duration),
                    "deezer_id": track_id,
                    "preview_url": track.get("preview", ""),
                    "album_cover": track.get("album", {}).get("cover_medium", ""),
                })
    except Exception as e:
        logger.error(f"Deezer qidiruvda xatolik: {e}")
    return results


async def search_music_youtube(query, max_results=20):
    """YouTube dan musiqa qidirish — fallback"""
    results = []
    try:
        import yt_dlp
        ydl_opts = _get_ydl_opts_search(max_results)

        def _search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                data = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
                if data and 'entries' in data:
                    for entry in data['entries']:
                        if not entry:
                            continue
                        title = entry.get('title', '')
                        video_id = entry.get('id', '')
                        if not title or not video_id:
                            continue
                        uploader = entry.get('uploader', entry.get('channel', ''))
                        duration = entry.get('duration') or 0
                        results.append({
                            "title": title,
                            "artist": uploader or "YouTube",
                            "url": f"https://www.youtube.com/watch?v={video_id}",
                            "source": "youtube",
                            "type": "ytdlp",
                            "duration": int(duration),
                        })
            return results

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, _search)
    except Exception as e:
        logger.error(f"YouTube qidiruvda xatolik: {e}")
    return results


async def search_music(query):
    """Deezer dan qidirish, bo'sh bo'lsa YouTube fallback"""
    results = await search_music_deezer(query, max_results=20)
    if not results:
        results = await search_music_youtube(query, max_results=20)
    return results


# =====================================================
# YouTube dan audio yuklab yuborish (umumiy funksiya)
# =====================================================

async def _download_audio_cobalt(url: str, tmp_dir: str) -> tuple:
    """Cobalt API orqali audio yuklash (tez va ishonchli)"""
    cobalt_url = os.getenv("COBALT_API_URL", "http://cobalt:9000")
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.post(
                cobalt_url,
                json={
                    "url": url,
                    "downloadMode": "audio",
                    "audioFormat": "mp3",
                    "audioBitrate": "192",
                    "filenameStyle": "basic",
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                }
            )
            if resp.status_code != 200:
                logger.warning(f"[Cobalt audio] HTTP {resp.status_code}")
                return None, None

            data = resp.json()
            status = data.get("status")
            if status == "error":
                logger.warning(f"[Cobalt audio] error: {data.get('error', {}).get('code', 'unknown')}")
                return None, None

            download_url = None
            if status in ("tunnel", "redirect"):
                download_url = data.get("url")
            elif status == "picker":
                items = data.get("picker", [])
                if items:
                    download_url = items[0].get("url")

            if not download_url:
                return None, None

            file_path = os.path.join(tmp_dir, "cobalt_audio.mp3")
            async with client.stream(
                "GET", download_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=300,
            ) as stream:
                if stream.status_code != 200:
                    return None, None
                with open(file_path, "wb") as f:
                    async for chunk in stream.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

            if os.path.getsize(file_path) < 1000:
                os.unlink(file_path)
                return None, None

            # Cobalt da info cheklangan — faqat fayl qaytaramiz
            info = {"title": data.get("filename", ""), "duration": 0}
            logger.info("[Cobalt audio] muvaffaqiyatli yuklandi")
            return info, file_path

    except httpx.ConnectError:
        logger.warning("[Cobalt audio] server ishlamayapti")
    except httpx.TimeoutException:
        logger.warning("[Cobalt audio] timeout")
    except Exception as e:
        logger.error(f"[Cobalt audio] xatolik: {e}")
    return None, None


def _normalize_cache_key(artist: str, title: str) -> str:
    """Artist+title dan cache kaliti yasash"""
    key = f"{artist} - {title}".lower().strip()
    # Ortiqcha belgilarni olib tashlash
    import re
    key = re.sub(r'[^\w\s-]', '', key)
    key = re.sub(r'\s+', ' ', key)
    return f"music:{key}"


async def download_and_send_audio(chat_id: int, url: str, title_hint: str = "", artist_hint: str = ""):
    """Musiqa yuklab yuborish — cache -> YouTube (proxysiz -> proxy fallback)"""
    caption = "✨ @tinchrobot – Tinchlikni xohlovchilar uchun!"

    # 1. Normalized cache tekshirish (artist+title bo'yicha)
    if title_hint and artist_hint:
        cache_key = _normalize_cache_key(artist_hint, title_hint)
        cached = await cache_db.get_file_id_by_url(cache_key)
        if cached:
            await bot.send_audio(chat_id=chat_id, audio=cached["file_id"], caption=caption)
            return True

    # 2. URL bo'yicha cache
    if not url.startswith("deezer:"):
        cached = await cache_db.get_file_id_by_url(url)
        if cached:
            await bot.send_audio(chat_id=chat_id, audio=cached["file_id"], caption=caption)
            return True

    # 3. Deezer URL bo'lsa — YouTube dan qidirish kerak
    yt_url = url
    if url.startswith("deezer:"):
        search_q = f"{artist_hint} {title_hint}".strip()
        if not search_q:
            return False
        yt_results = await search_music_youtube(search_q, max_results=3)
        if not yt_results:
            return False
        yt_url = yt_results[0]["url"]

        # YouTube URL bo'yicha ham cache tekshirish
        cached = await cache_db.get_file_id_by_url(yt_url)
        if cached:
            await bot.send_audio(chat_id=chat_id, audio=cached["file_id"], caption=caption)
            # Normalized cache ham saqlash
            if title_hint and artist_hint:
                await cache_db.add_cache("youtube", _normalize_cache_key(artist_hint, title_hint), cached["file_id"], "audio")
            return True

    tmp_dir = tempfile.mkdtemp(prefix="ytmusic_")
    try:
        info = None
        file_path = None
        import yt_dlp

        def _yt_download(use_proxy=False):
            ydl_opts = _get_ydl_opts_download(tmp_dir, use_proxy=use_proxy)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                _info = ydl.extract_info(yt_url, download=True)
                if _info is None:
                    return None, None
                _file_path = ydl.prepare_filename(_info)
                if not os.path.exists(_file_path):
                    base, _ = os.path.splitext(_file_path)
                    for ext in ['.m4a', '.mp3', '.webm', '.opus']:
                        candidate = base + ext
                        if os.path.exists(candidate):
                            _file_path = candidate
                            break
                return _info, _file_path

        loop = asyncio.get_event_loop()

        # Proxysiz (tez)
        try:
            info, file_path = await loop.run_in_executor(None, lambda: _yt_download(False))
        except Exception:
            info, file_path = None, None

        # WARP proxy fallback
        if not info or not file_path or not os.path.exists(str(file_path) if file_path else ''):
            try:
                info, file_path = await loop.run_in_executor(None, lambda: _yt_download(True))
            except Exception:
                info, file_path = None, None

        if not info or not file_path or not os.path.exists(file_path):
            return False

        title = title_hint or info.get('title', 'Audio')
        artist = artist_hint or info.get('artist') or info.get('uploader', '')
        duration = int(info.get('duration') or 0)
        thumbnail_url = info.get('thumbnail', '')

        # Thumbnail
        thumb_data = None
        if thumbnail_url:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    thumb_resp = await client.get(thumbnail_url)
                    if thumb_resp.status_code == 200:
                        thumb_path = os.path.join(tmp_dir, "thumb.jpg")
                        with open(thumb_path, "wb") as f:
                            f.write(thumb_resp.content)
                        thumb_data = thumb_path
            except Exception:
                pass

        file_size = os.path.getsize(file_path)
        if file_size > 50 * 1024 * 1024:
            return False

        # Fayl kengaytmasini aniqlash
        _, ext = os.path.splitext(file_path)
        file_ext = ext.lstrip('.') or 'm4a'

        with open(file_path, "rb") as f:
            audio_data = BytesIO(f.read())
            audio_data.name = f"{artist} - {title}.{file_ext}"
            audio_data.seek(0)

            kwargs = {
                "chat_id": chat_id,
                "audio": audio_data,
                "caption": caption,
                "title": title[:64],
                "performer": artist[:64],
                "duration": duration,
            }
            if thumb_data and os.path.exists(thumb_data):
                kwargs["thumb"] = open(thumb_data, "rb")

            try:
                sent_msg = await bot.send_audio(**kwargs)
                if sent_msg.audio:
                    file_id = sent_msg.audio.file_id
                    # URL bo'yicha cache
                    await cache_db.add_cache("youtube", yt_url, file_id, "audio")
                    # Normalized cache (artist+title)
                    if title and artist:
                        cache_key = _normalize_cache_key(artist, title)
                        await cache_db.add_cache("youtube", cache_key, file_id, "audio")
            finally:
                if "thumb" in kwargs and hasattr(kwargs["thumb"], "close"):
                    kwargs["thumb"].close()

        return True
    except Exception as e:
        logger.error(f"Audio yuklash xatosi: {e}", exc_info=True)
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# =====================================================
# Shazam — musiqa aniqlash
# =====================================================

async def recognize_audio_shazam(audio_path: str) -> dict | None:
    """Shazam orqali audio fayldan musiqa aniqlash"""
    try:
        from shazamio import Shazam
        shazam = Shazam()
        result = await shazam.recognize(audio_path)

        if result and 'track' in result:
            track = result['track']
            album = ''
            try:
                sections = track.get('sections', [])
                if sections and 'metadata' in sections[0]:
                    metadata = sections[0]['metadata']
                    if metadata:
                        album = metadata[0].get('text', '')
            except (IndexError, KeyError):
                pass

            return {
                'title': track.get('title', ''),
                'artist': track.get('subtitle', ''),
                'album': album,
                'cover_url': track.get('images', {}).get('coverarthq', ''),
                'genre': track.get('genres', {}).get('primary', ''),
            }
    except ImportError:
        logger.error("shazamio o'rnatilmagan!")
    except Exception as e:
        logger.error(f"Shazam xatosi: {e}", exc_info=True)
    return None


async def extract_audio_from_video(video_path: str) -> str | None:
    """Video fayldan audio chiqarib olish (ffmpeg)"""
    try:
        audio_path = video_path.rsplit('.', 1)[0] + '_audio.ogg'
        process = await asyncio.create_subprocess_exec(
            'ffmpeg', '-i', video_path,
            '-vn', '-acodec', 'libopus', '-b:a', '128k',
            '-t', '25', '-y', audio_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()
        if process.returncode == 0 and os.path.exists(audio_path):
            return audio_path
    except Exception as e:
        logger.error(f"Audio ajratish xatosi: {e}")
    return None


async def _shazam_and_show(chat_id: int, audio_path: str, status_msg):
    """Shazam bilan aniqlash va natija ko'rsatish + Yuklash tugmasi"""
    result = await recognize_audio_shazam(audio_path)

    if not result or not result.get('title'):
        await status_msg.edit_text(
            "😔 Musiqa aniqlab bo'lmadi.\n\n"
            "💡 Musiqani aniqroq yozib yuboring — fondan shovqin kam bo'lsa yaxshi natija beradi.",
        )
        return

    # Natija matni
    text_parts = [
        "🎵 Musiqa topildi!\n",
        f"🎤 Ijrochi: {result['artist']}",
        f"🎶 Nomi: {result['title']}",
    ]
    if result.get('album'):
        text_parts.append(f"💿 Albom: {result['album']}")
    if result.get('genre'):
        text_parts.append(f"🏷 Janr: {result['genre']}")

    text = "\n".join(text_parts)

    # YouTube dan birinchi natijani topish (faqat URL saqlash uchun)
    search_query = f"{result['artist']} {result['title']}"
    yt_results = await search_music_youtube(search_query, max_results=3)

    markup = None
    if yt_results:
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

    # Cover bilan ko'rsatish
    if result.get('cover_url'):
        try:
            await status_msg.delete()
            await bot.send_photo(
                chat_id=chat_id,
                photo=result['cover_url'],
                caption=text,
                reply_markup=markup,
            )
            return
        except Exception:
            pass

    await status_msg.edit_text(text, reply_markup=markup)


# =====================================================
# Ovozli xabar handler — Shazam + avtomatik yuklash
# =====================================================

@dp.message_handler(content_types=[ContentType.VOICE, ContentType.AUDIO, ContentType.VIDEO_NOTE])
async def handle_voice_shazam(message: types.Message):
    """Ovozli xabar yuborilganda Shazam + avtomatik yuklash"""
    status_msg = await message.reply("🎵 Musiqa aniqlanmoqda...")
    tmp_dir = tempfile.mkdtemp(prefix="shazam_")

    try:
        if message.voice:
            file_info = await bot.get_file(message.voice.file_id)
        elif message.audio:
            file_info = await bot.get_file(message.audio.file_id)
        elif message.video_note:
            file_info = await bot.get_file(message.video_note.file_id)
        else:
            await status_msg.edit_text("Audio fayl topilmadi.")
            return

        file_ext = '.ogg'
        if message.audio and message.audio.file_name:
            _, ext = os.path.splitext(message.audio.file_name)
            if ext:
                file_ext = ext

        audio_path = os.path.join(tmp_dir, f"input{file_ext}")
        await bot.download_file(file_info.file_path, destination=audio_path)

        await _shazam_and_show(message.chat.id, audio_path, status_msg)

    except Exception as e:
        logger.error(f"Shazam handler xatosi: {e}", exc_info=True)
        try:
            await status_msg.edit_text("⚠️ Musiqa aniqlashda xatolik yuz berdi.")
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# =====================================================
# Shazam natijasidan yuklash callback
# =====================================================

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("shazam_dl:"))
async def shazam_download_callback(callback: CallbackQuery):
    """Shazam natijasidan YouTube dan yuklab berish"""
    chat_id = int(callback.data.split(":")[1])
    shazam_key = f"shazam_dl_{chat_id}"
    data = user_results.pop(shazam_key, None)

    if not data:
        await callback.answer("Ma'lumot topilmadi.")
        return

    await callback.answer("Yuklanmoqda...")

    success = await download_and_send_audio(
        chat_id, data['url'],
        title_hint=data.get('title', ''),
        artist_hint=data.get('artist', ''),
    )
    if not success:
        await bot.send_message(chat_id, "⚠️ Yuklab bo'lmadi. Qayta urinib ko'ring.")


# =====================================================
# Video xabar handler — videodagi musiqani aniqlash
# =====================================================

@dp.message_handler(content_types=[ContentType.VIDEO])
async def handle_video_shazam(message: types.Message):
    """Video yuborilganda musiqani aniqlash tugmasi"""
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(
        text="🎵 Musiqani aniqlash",
        callback_data=f"vid_shazam:{message.message_id}:{message.chat.id}",
    ))
    await message.reply("🎬 Videodagi musiqani aniqlash:", reply_markup=markup)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("vid_shazam:"))
async def video_shazam_callback(callback: CallbackQuery):
    """Videodagi musiqani Shazam orqali aniqlash + avtomatik yuklash"""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Noto'g'ri ma'lumot.")
        return

    chat_id = int(parts[2])
    await callback.answer("Aniqlanmoqda...")
    status_msg = await callback.message.edit_text("🎵 Videodagi musiqa aniqlanmoqda...")

    tmp_dir = tempfile.mkdtemp(prefix="vid_shazam_")

    try:
        reply_msg = callback.message.reply_to_message
        if not reply_msg or not reply_msg.video:
            await status_msg.edit_text("⚠️ Video topilmadi.")
            return

        video = reply_msg.video
        if video.file_size and video.file_size > 20 * 1024 * 1024:
            await status_msg.edit_text("⚠️ Video juda katta (20MB gacha).")
            return

        file_info = await bot.get_file(video.file_id)
        video_path = os.path.join(tmp_dir, "video.mp4")
        await bot.download_file(file_info.file_path, destination=video_path)

        audio_path = await extract_audio_from_video(video_path)
        if not audio_path:
            await status_msg.edit_text("⚠️ Videodan audio ajratib bo'lmadi.")
            return

        await _shazam_and_show(chat_id, audio_path, status_msg)

    except Exception as e:
        logger.error(f"Video Shazam xatosi: {e}", exc_info=True)
        try:
            await status_msg.edit_text("⚠️ Xatolik yuz berdi.")
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# =====================================================
# Matn qidiruv handler
# =====================================================

@dp.message_handler()
async def handle_message(message: types.Message):
    search_query = message.text.strip()
    if not search_query:
        await message.reply("Iltimos, qidiruv so'zini kiriting.")
        return

    status_msg = await message.reply("🔍 Qidirilmoqda...")

    all_results = await search_music(search_query)

    if all_results:
        user_results[message.chat.id] = {
            "results": all_results,
            "current_page": 1,
            "query": search_query,
        }
        try:
            await status_msg.delete()
        except Exception:
            pass
        await send_results_page(message.chat.id)
    else:
        await status_msg.edit_text("Hech qanday natija topilmadi. Boshqa so'z bilan urinib ko'ring.")


# =====================================================
# Natijalar sahifasi
# =====================================================

async def send_results_page(chat_id):
    data = user_results.get(chat_id)
    if not data:
        return

    results = data["results"]
    page = data["current_page"]
    items_per_page = 10
    total_pages = (len(results) - 1) // items_per_page + 1
    search_query = data.get("query", "Natijalar")

    start_index = (page - 1) * items_per_page
    end_index = start_index + items_per_page
    page_results = results[start_index:end_index]

    markup = InlineKeyboardMarkup(row_width=5)
    buttons = []
    for idx, info in enumerate(page_results, start=1):
        result_id = start_index + idx - 1
        buttons.append(
            InlineKeyboardButton(text=f"{idx}", callback_data=f"download:{result_id}:{chat_id}")
        )
    markup.add(*buttons)

    pagination_buttons = []
    if page > 1:
        pagination_buttons.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"page:{page - 1}:{chat_id}")
        )
    if page < total_pages:
        pagination_buttons.append(
            InlineKeyboardButton(text="➡️", callback_data=f"page:{page + 1}:{chat_id}")
        )

    clear_button = InlineKeyboardButton(text="❌", callback_data=f"clear:{chat_id}")
    if pagination_buttons:
        pagination_buttons.append(clear_button)
        markup.add(*pagination_buttons)
    else:
        markup.add(clear_button)

    lines = []
    for idx, info in enumerate(page_results, start=1):
        dur = int(info.get("duration") or 0)
        dur_str = f" ({dur // 60}:{dur % 60:02d})" if dur else ""
        lines.append(f"{idx}. {info['artist']} - {info['title']}{dur_str}")

    response_text = (
        f"🔍 **{search_query}** ({page}/{total_pages}):\n\n"
        + "\n".join(lines)
    )

    old_message_id = data.get("message_id")
    if old_message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_message_id)
        except Exception:
            pass

    sent_message = await bot.send_message(chat_id, response_text, reply_markup=markup, parse_mode="Markdown")
    user_results[chat_id]["message_id"] = sent_message.message_id


# =====================================================
# Pagination & Clear
# =====================================================

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("page:"))
async def pagination_callback_handler(callback_query: CallbackQuery):
    data_parts = callback_query.data.split(":")
    if len(data_parts) == 3:
        _, page_str, chat_id_str = data_parts
        page = int(page_str)
        chat_id = int(chat_id_str)
        user_data = user_results.get(chat_id)
        if user_data:
            user_data["current_page"] = page
            await send_results_page(chat_id)
            await callback_query.answer()
        else:
            await callback_query.answer("Ma'lumot topilmadi.")
    else:
        await callback_query.answer("Noto'g'ri ma'lumot.")


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("clear:"))
async def clear_callback_handler(callback_query: CallbackQuery):
    data_parts = callback_query.data.split(":")
    if len(data_parts) == 2:
        chat_id = int(data_parts[1])
        user_data = user_results.get(chat_id)
        if user_data:
            msg_id = user_data.get("message_id")
            if msg_id:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except Exception:
                    pass
            user_results.pop(chat_id, None)
            await callback_query.answer("O'chirildi.")
        else:
            await callback_query.answer("Ma'lumot topilmadi.")
    else:
        await callback_query.answer("Noto'g'ri ma'lumot.")


# =====================================================
# Download callback
# =====================================================

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("download:"))
async def download_callback_handler(callback_query: CallbackQuery):
    data_parts = callback_query.data.split(":")
    if len(data_parts) == 3:
        _, result_id_str, chat_id_str = data_parts
        result_id = int(result_id_str)
        chat_id = int(chat_id_str)

        user_data = user_results.get(chat_id)
        if user_data and 0 <= result_id < len(user_data["results"]):
            music_info = user_data["results"][result_id]
            url = music_info["url"]

            await callback_query.answer("Yuklanmoqda...")

            success = await download_and_send_audio(
                callback_query.message.chat.id, url,
                title_hint=music_info.get('title', ''),
                artist_hint=music_info.get('artist', ''),
            )
            if not success:
                await bot.send_message(
                    callback_query.message.chat.id,
                    "⚠️ Yuklab bo'lmadi. Qayta urinib ko'ring."
                )
        else:
            await callback_query.answer("Topilmadi.")
    else:
        await callback_query.answer("Noto'g'ri ma'lumot.")
