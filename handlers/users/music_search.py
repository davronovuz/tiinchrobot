import asyncio
import os
import httpx
from bs4 import BeautifulSoup
from aiogram import types
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from io import BytesIO
import logging
from loader import dp, bot, cache_db

from keyboards.default.menu_i import world_track, top_track, main_btn
from utils.misc.download_file import world_music, main_data, top_music, new_trek

logger = logging.getLogger(__name__)

COOKIES_FILE = os.getenv("COOKIES_FILE", "/app/cookies.txt")


# /tiktok, /top, /new komandalari
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


# Foydalanuvchi qidiruv natijalarini saqlash uchun lug'at
user_results = {}


# === Qidiruv funksiyalari (Uz saytlar + YouTube Music) ===

async def search_music_muztv(query):
    search_url = f"http://muztv.uz/index.php?do=search&subaction=search&story={query}"
    results = []
    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        try:
            response = await client.get(
                search_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15.0
            )
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                for item in soup.find_all("div", class_="play-item"):
                    title = item.get("data-title")
                    artist = item.get("data-artist")
                    url = item.get("data-track")
                    if title and artist and url:
                        if url.startswith("/"):
                            url = f"https://muztv.uz{url}"
                        results.append({"title": title, "artist": artist, "url": url, "source": "muztv", "type": "direct"})
        except Exception as e:
            logger.error(f"muztv.uz xatolik: {e}")
    return results


async def search_music_xitmuzon(query):
    search_url = f"https://xitmuzon.net/index.php?do=search&subaction=search&story={query}"
    results = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.get(
                search_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15.0
            )
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                for item in soup.find_all("div", class_="track-item"):
                    title = item.get("data-title")
                    artist = item.get("data-artist")
                    dl_link = item.find("a", class_="track-dl")
                    if title and artist and dl_link:
                        url = dl_link.get("href", "")
                        if url.startswith("/"):
                            url = f"https://xitmuzon.net{url}"
                        if url:
                            results.append({"title": title, "artist": artist, "url": url, "source": "xitmuzon", "type": "direct"})
        except Exception as e:
            logger.error(f"xitmuzon.net xatolik: {e}")
    return results


async def search_music_uzhits(query):
    search_url = f"https://uzhits.net/index.php?do=search&subaction=search&story={query}"
    results = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.get(
                search_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15.0
            )
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                for item in soup.find_all("div", class_="track-item"):
                    title = item.get("data-title")
                    artist = item.get("data-artist")
                    url = item.get("data-track")
                    if title and artist and url:
                        if url.startswith("/"):
                            url = f"https://uzhits.net{url}"
                        results.append({"title": title, "artist": artist, "url": url, "source": "uzhits", "type": "direct"})
        except Exception as e:
            logger.error(f"uzhits.net xatolik: {e}")
    return results


async def search_music_youtube(query):
    """YouTube Music dan musiqa qidirish (yt-dlp orqali) — HAMMA musiqa topiladi"""
    results = []
    try:
        import yt_dlp
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'default_search': 'ytsearch15',
            'socket_timeout': 15,
        }
        if os.path.exists(COOKIES_FILE):
            ydl_opts['cookiefile'] = COOKIES_FILE

        def _search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_results = ydl.extract_info(f"ytsearch15:{query} audio", download=False)
                if search_results and 'entries' in search_results:
                    for entry in search_results['entries']:
                        if entry is None:
                            continue
                        title = entry.get('title', '')
                        uploader = entry.get('uploader', entry.get('channel', ''))
                        video_url = entry.get('url', '')
                        video_id = entry.get('id', '')
                        duration = entry.get('duration', 0)

                        if not title or not video_id:
                            continue

                        # Audio/Music kontentni afzal ko'ramiz
                        results.append({
                            "title": title,
                            "artist": uploader or "YouTube",
                            "url": f"https://www.youtube.com/watch?v={video_id}",
                            "source": "youtube",
                            "type": "ytdlp",
                            "duration": duration or 0,
                        })
            return results

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, _search)
    except Exception as e:
        logger.error(f"YouTube Music qidiruvda xatolik: {e}")
    return results


async def search_music(query):
    """Barcha manbalardan parallel qidirish"""
    tasks = [
        search_music_muztv(query),
        search_music_xitmuzon(query),
        search_music_uzhits(query),
        search_music_youtube(query),
    ]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    all_results = []
    seen_titles = set()

    # Avval O'zbek saytlar natijalarini qo'shamiz (tezroq yuklanadi)
    for results in results_list[:3]:
        if isinstance(results, Exception):
            logger.error(f"Qidiruv xatosi: {results}")
            continue
        for item in results:
            key = f"{item['artist'].lower().strip()}-{item['title'].lower().strip()}"
            if key not in seen_titles:
                seen_titles.add(key)
                all_results.append(item)

    # Keyin YouTube natijalarini qo'shamiz
    yt_results = results_list[3] if not isinstance(results_list[3], Exception) else []
    if isinstance(yt_results, Exception):
        logger.error(f"YouTube qidiruv xatosi: {yt_results}")
        yt_results = []
    for item in yt_results:
        key = f"{item['artist'].lower().strip()}-{item['title'].lower().strip()}"
        if key not in seen_titles:
            seen_titles.add(key)
            all_results.append(item)

    return all_results


# === Xabarni qayta ishlash (musiqa qidirish) ===

@dp.message_handler()
async def handle_message(message: types.Message):
    search_query = message.text.strip()
    if not search_query:
        await message.reply("Iltimos, qidiruv so'zini kiriting.")
        return

    await bot.send_chat_action(message.chat.id, "typing")

    all_results = await search_music(search_query)

    if all_results:
        user_results[message.chat.id] = {
            "results": all_results,
            "current_page": 1,
            "query": search_query,
        }
        await send_results_page(message.chat.id)
    else:
        await message.reply("Hech qanday natija topilmadi. Boshqa kalit so'z bilan urinib ko'ring.")


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
        # YouTube natijalar uchun YT belgisi
        label = f"{idx}"
        if info.get("source") == "youtube":
            label = f"▶{idx}"
        buttons.append(
            InlineKeyboardButton(text=label, callback_data=f"download:{result_id}:{chat_id}")
        )
    markup.add(*buttons)

    pagination_buttons = []
    if page > 1:
        pagination_buttons.append(
            InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"page:{page - 1}:{chat_id}")
        )
    if page < total_pages:
        pagination_buttons.append(
            InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"page:{page + 1}:{chat_id}")
        )

    clear_button = InlineKeyboardButton(text="❌", callback_data=f"clear:{chat_id}")
    if pagination_buttons:
        pagination_buttons.append(clear_button)
        markup.add(*pagination_buttons)
    else:
        markup.add(clear_button)

    # Natijalar ro'yxatini chiqarish
    lines = []
    for idx, info in enumerate(page_results, start=1):
        source_tag = ""
        if info.get("source") == "youtube":
            source_tag = " [YT]"
        dur = info.get("duration", 0)
        dur_str = f" ({dur // 60}:{dur % 60:02d})" if dur else ""
        lines.append(f"{idx}. {info['artist']} - {info['title']}{dur_str}{source_tag}")

    total_text = f"Jami: {len(results)} natija"
    response_text = (
        f"🔍 **{search_query}** (sahifa {page}/{total_pages}):\n"
        f"_{total_text}_\n\n"
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
        _, chat_id_str = data_parts
        chat_id = int(chat_id_str)
        user_data = user_results.get(chat_id)
        if user_data:
            message_id = user_data.get("message_id")
            if message_id:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                except Exception:
                    pass
            user_results.pop(chat_id, None)
            await callback_query.answer("Natijalar o'chirildi.")
        else:
            await callback_query.answer("Ma'lumot topilmadi.")
    else:
        await callback_query.answer("Noto'g'ri ma'lumot.")


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
            source_type = music_info.get("type", "direct")

            # Avval cache tekshirish
            cached = await cache_db.get_file_id_by_url(url)
            if cached:
                caption = "✨ @tinchrobot – Tinchlikni xohlovchilar uchun!"
                await bot.send_audio(
                    chat_id=callback_query.message.chat.id,
                    audio=cached["file_id"],
                    caption=caption,
                )
                await callback_query.answer()
                return

            await callback_query.answer("Yuklab olinmoqda, biroz kuting...")

            if source_type == "ytdlp":
                # YouTube dan yt-dlp orqali audio yuklab olish
                await _download_and_send_ytdlp_audio(callback_query, music_info)
            else:
                # To'g'ridan-to'g'ri URL dan yuklab olish
                await _download_and_send_direct(callback_query, music_info)
        else:
            await callback_query.answer("Yuklab olish havolasi topilmadi.")
    else:
        await callback_query.answer("Noto'g'ri ma'lumot.")


async def _download_and_send_direct(callback_query, music_info):
    """O'zbek saytlardan to'g'ridan-to'g'ri mp3 yuklab olish"""
    url = music_info["url"]
    async with httpx.AsyncClient(follow_redirects=True, verify=False) as client:
        try:
            response = await client.get(
                url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60.0
            )
            if response.status_code == 200:
                file_data = BytesIO(response.content)
                file_data.seek(0)
                file_data.name = f"{music_info['artist']} - {music_info['title']}.mp3"

                caption = "✨ @tinchrobot – Tinchlikni xohlovchilar uchun!"

                sent_msg = await bot.send_audio(
                    chat_id=callback_query.message.chat.id,
                    audio=file_data,
                    caption=caption,
                    title=music_info["title"],
                    performer=music_info["artist"],
                )
                # Cache ga saqlash
                if sent_msg.audio:
                    await cache_db.add_cache(
                        music_info["source"], url, sent_msg.audio.file_id, "audio"
                    )
            else:
                await callback_query.message.answer("Qo'shiqni yuklab olishda xatolik yuz berdi.")
        except Exception as e:
            logger.error(f"Direct yuklab olish xatosi: {e}")
            await callback_query.message.answer("Qo'shiqni yuklab olishda xatolik yuz berdi.")


async def _download_and_send_ytdlp_audio(callback_query, music_info):
    """YouTube dan yt-dlp orqali audio yuklab olish"""
    import tempfile
    url = music_info["url"]
    tmp_dir = tempfile.mkdtemp(prefix="ytmusic_")

    try:
        import yt_dlp

        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': os.path.join(tmp_dir, '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'retries': 3,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            },
        }

        if os.path.exists(COOKIES_FILE):
            ydl_opts['cookiefile'] = COOKIES_FILE

        # YouTube extractor sozlamalari
        ydl_opts['extractor_args'] = {
            'youtube': {
                'player_client': ['android,web'],
                'player_skip': ['webpage', 'configs'],
            }
        }

        try:
            import curl_cffi
            ydl_opts['impersonate'] = 'chrome'
        except ImportError:
            pass

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    return None, None

                # mp3 faylni topish
                file_path = ydl.prepare_filename(info)
                base, _ = os.path.splitext(file_path)
                mp3_path = base + '.mp3'
                if os.path.exists(mp3_path):
                    file_path = mp3_path
                elif not os.path.exists(file_path):
                    for ext in ['.mp3', '.m4a', '.webm', '.opus']:
                        candidate = base + ext
                        if os.path.exists(candidate):
                            file_path = candidate
                            break

                return info, file_path

        loop = asyncio.get_event_loop()
        info, file_path = await loop.run_in_executor(None, _download)

        if not info or not file_path or not os.path.exists(file_path):
            await callback_query.message.answer("YouTube dan audio yuklab olishda xatolik.")
            return

        title = info.get('title', music_info.get('title', 'Audio'))
        artist = info.get('artist') or info.get('uploader', music_info.get('artist', ''))
        duration = info.get('duration', 0)
        thumbnail_url = info.get('thumbnail', '')

        # Thumbnail yuklab olish
        thumb_data = None
        if thumbnail_url:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
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
            await callback_query.message.answer("Audio fayl hajmi juda katta (>50MB).")
            return

        caption = "✨ @tinchrobot – Tinchlikni xohlovchilar uchun!"

        with open(file_path, "rb") as f:
            audio_data = BytesIO(f.read())
            audio_data.name = f"{artist} - {title}.mp3"
            audio_data.seek(0)

            kwargs = {
                "chat_id": callback_query.message.chat.id,
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
                    await cache_db.add_cache("youtube", url, sent_msg.audio.file_id, "audio")
            finally:
                if "thumb" in kwargs and hasattr(kwargs["thumb"], "close"):
                    kwargs["thumb"].close()

    except Exception as e:
        logger.error(f"YouTube audio yuklab olish xatosi: {e}", exc_info=True)
        await callback_query.message.answer("YouTube dan audio yuklab olishda xatolik yuz berdi.")
    finally:
        # Vaqtinchalik fayllarni tozalash
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
