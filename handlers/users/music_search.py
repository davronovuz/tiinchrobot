import asyncio
import os
import re
import tempfile
import shutil
import logging
import time
from collections import defaultdict

import httpx
from aiogram import types
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ContentType,
)

import json

import loader
from loader import dp, bot, cache_db
from utils.misc.download_file import world_music, top_music, new_trek
from utils import health, state_store
from utils.video_downloader import DOWNLOAD_EXECUTOR
from utils.tg_files import sendable, download_tg_file, SHARED_TEMP_DIR

logger = logging.getLogger(__name__)

BGUTIL_URL = os.getenv("BGUTIL_URL", "http://bgutil:4416")
WARP_PROXY = "socks5://warp:9091"

# =====================================================
# Rate limiter — foydalanuvchi boshiga yuklash cheklovi
# =====================================================
_user_download_times = defaultdict(list)
_USER_RATE_LIMIT = 5         # max yuklashlar
_USER_RATE_WINDOW = 60       # sekund ichida
_download_semaphore = asyncio.Semaphore(10)  # bir vaqtda max 10 ta yuklash


def _check_rate_limit(user_id: int) -> bool:
    """Foydalanuvchi rate limitdan o'tganmi tekshirish. True = ruxsat"""
    now = time.monotonic()
    times = _user_download_times[user_id]
    # Eski yozuvlarni tozalash
    _user_download_times[user_id] = [t for t in times if now - t < _USER_RATE_WINDOW]
    if len(_user_download_times[user_id]) >= _USER_RATE_LIMIT:
        return False
    _user_download_times[user_id].append(now)
    return True


# =====================================================
# /tiktok, /top, /new — chart komandalari
#
# MUHIM: ilgari bu handlerlar callback FILTRI ichida saytni scrape qilardi
# (lambda x: x.data in [i['id'] for i in world_music()]) — ya'ni botdagi
# HAR BIR callback query 3 ta HTTP scrape'ni sinxron ishga tushirar va
# event loop'ni bloklardi. Endi natijalar cache'lanadi va executor'da olinadi.
# =====================================================

_CHART_TTL = 3600  # 1 soat


async def _fetch_chart(kind: str) -> list:
    """Chartni cache'dan yoki manbadan olish (bloklamaydi)"""
    cache_key = f"chart:{kind}"
    cached = await state_store.get_state(cache_key)
    if cached:
        return cached

    scrapers = {"tiktok": world_music, "top": top_music, "new": new_trek}
    scraper = scrapers.get(kind)

    items = []
    if scraper:
        try:
            loop = asyncio.get_event_loop()
            raw = await asyncio.wait_for(
                loop.run_in_executor(DOWNLOAD_EXECUTOR, scraper), timeout=12
            )
            for entry in raw or []:
                title = entry.get("title", "")
                if not title:
                    continue
                items.append({
                    "title": title,
                    "artist": entry.get("artist", ""),
                    "url": f"direct:{entry['track']}" if entry.get("track") else "deezer:0",
                    "source": "chart",
                    "type": "chart",
                    "duration": 0,
                })
        except asyncio.TimeoutError:
            logger.warning(f"[chart] {kind} timeout")
        except Exception as e:
            logger.error(f"[chart] {kind} xatosi: {e}")

    # Manba ishlamasa — Deezer global chart zaxira
    if not items:
        items = await _fetch_deezer_chart(limit=20)

    if items:
        await state_store.set_state(cache_key, items, ttl=_CHART_TTL)
    return items


async def _fetch_deezer_chart(limit: int = 20) -> list:
    """Deezer global chart — manba ishlamaganda zaxira"""
    try:
        from utils.video_downloader import get_http_client
        resp = await get_http_client().get(
            "https://api.deezer.com/chart/0/tracks", params={"limit": limit}, timeout=10,
        )
        if resp.status_code != 200:
            return []
        out = []
        for tr in resp.json().get("data", []):
            title = tr.get("title", "")
            if not title:
                continue
            out.append({
                "title": title,
                "artist": (tr.get("artist") or {}).get("name", ""),
                "url": f"deezer:{tr.get('id', 0)}",
                "source": "deezer",
                "type": "deezer",
                "duration": int(tr.get("duration") or 0),
                "album_cover": (tr.get("album") or {}).get("cover_medium", ""),
            })
        return out
    except Exception as e:
        logger.error(f"[Deezer chart] xatosi: {e}")
    return []


async def _show_chart(msg: types.Message, kind: str, header: str):
    status = await msg.answer("⏳ Ro'yxat tayyorlanmoqda...")
    items = await _fetch_chart(kind)

    if not items:
        await status.edit_text("😔 Ro'yxatni olishning iloji bo'lmadi. Keyinroq urinib ko'ring.")
        return

    await state_store.set_state(f"res:{msg.chat.id}", {
        "results": items,
        "current_page": 1,
        "query": header,
    }, ttl=_RESULTS_TTL)

    try:
        await status.delete()
    except Exception:
        pass
    await send_results_page(msg.chat.id)


@dp.message_handler(commands='tiktok')
async def tik_tok_handler(msg: types.Message):
    await _show_chart(msg, "tiktok", "🎬 TikTok musiqalari")


@dp.message_handler(commands='top')
async def top_handler(msg: types.Message):
    await _show_chart(msg, "top", "🔥 Top musiqalar")


@dp.message_handler(commands='new')
async def new_music_handler(msg: types.Message):
    await _show_chart(msg, "new", "🆕 Yangi musiqalar")


@dp.message_handler(commands=['chart', 'global'])
async def global_chart_handler(msg: types.Message):
    status = await msg.answer("⏳ Global chart olinmoqda...")
    items = await _fetch_deezer_chart(limit=25)
    if not items:
        await status.edit_text("😔 Chartni olishning iloji bo'lmadi.")
        return
    await state_store.set_state(f"res:{msg.chat.id}", {
        "results": items,
        "current_page": 1,
        "query": "🌍 Global Top",
    }, ttl=_RESULTS_TTL)
    try:
        await status.delete()
    except Exception:
        pass
    await send_results_page(msg.chat.id)


@dp.callback_query_handler(lambda msg: msg.data == 'remove')
async def remove(callback: types.CallbackQuery):
    await callback.message.delete()


# =====================================================
# Foydalanuvchi qidiruv natijalarini saqlash
# =====================================================
# Qidiruv natijalari Redis da saqlanadi (bot restart bo'lsa yo'qolmaydi).
# Kalitlar: "res:<chat_id>" — qidiruv natijalari, "shazam:<chat_id>" — Shazam topilmasi
_RESULTS_TTL = 3600      # 1 soat
_SHAZAM_TTL = 1800       # 30 daqiqa


# =====================================================
# YouTube Music qidiruv
# =====================================================

def _yt_base_opts(use_proxy=False):
    """YouTube uchun umumiy opsiyalar — android_vr + bgutil + aria2c"""
    opts = {
        'concurrent_fragment_downloads': 8,
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
        opts['nocheckcertificate'] = True
        opts['legacy_server_connect'] = True
        opts['concurrent_fragment_downloads'] = 16
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
    retries = 5 if use_proxy else 3
    opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'outtmpl': os.path.join(tmp_dir, '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': retries,
        'fragment_retries': retries,
        'extractor_retries': retries,
    }
    opts.update(_yt_base_opts(use_proxy=use_proxy))
    if not use_proxy:
        # aria2c — parallel ulanish bilan 3-5x tez yuklash
        opts['external_downloader'] = {'http': 'aria2c', 'https': 'aria2c'}
        opts['external_downloader_args'] = {'aria2c': ['-x', '8', '-s', '8', '-k', '1M']}
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


async def search_music_itunes(query, max_results=20):
    """iTunes Search API — bepul, kalitsiz, ~0.7s, toza metadata"""
    results = []
    try:
        from utils.video_downloader import get_http_client
        resp = await get_http_client().get(
            "https://itunes.apple.com/search",
            params={"term": query, "media": "music", "limit": max_results},
            timeout=8,
        )
        if resp.status_code != 200:
            return results
        for tr in resp.json().get("results", []):
            title = tr.get("trackName") or ""
            artist = tr.get("artistName") or ""
            if not title:
                continue
            results.append({
                "title": title,
                "artist": artist,
                "url": f"itunes:{tr.get('trackId', 0)}",
                "source": "itunes",
                "type": "meta",
                "duration": int((tr.get("trackTimeMillis") or 0) // 1000),
                "album_cover": tr.get("artworkUrl100", ""),
            })
    except Exception as e:
        logger.error(f"iTunes qidiruvda xatolik: {e}")
    return results


def _parse_yt_search_html(html: str, max_results: int) -> list:
    """ytInitialData dan videoRenderer larni ajratib olish"""
    m = re.search(r'var ytInitialData = (\{.*?\});</script>', html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except Exception:
        return []

    def _walk(obj):
        if isinstance(obj, dict):
            if "videoRenderer" in obj:
                yield obj["videoRenderer"]
            for v in obj.values():
                yield from _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from _walk(v)

    out = []
    for v in _walk(data):
        vid = v.get("videoId")
        if not vid:
            continue
        try:
            title = v["title"]["runs"][0]["text"]
        except (KeyError, IndexError, TypeError):
            continue
        try:
            channel = (v.get("ownerText") or v.get("longBylineText") or {})["runs"][0]["text"]
        except (KeyError, IndexError, TypeError):
            channel = "YouTube"

        duration = 0
        dur_text = (v.get("lengthText") or {}).get("simpleText")
        if dur_text:
            parts = dur_text.split(":")
            try:
                for pnum in parts:
                    duration = duration * 60 + int(pnum)
            except ValueError:
                duration = 0
        # Jonli efir yoki 15 daqiqadan uzun — musiqa emas
        if not duration or duration > 900:
            continue

        # "Artist - Title" ko'rinishidagi sarlavhani ajratish (kanal nomi o'rniga)
        artist = channel
        clean_title = title
        if " - " in title:
            left, right = title.split(" - ", 1)
            if 2 <= len(left.strip()) <= 40 and right.strip():
                artist = left.strip()
                clean_title = right.strip()
        # Ortiqcha teglarni tozalash
        clean_title = re.sub(
            r'\s*[\(\[](?:official|lyrics?|audio|video|music|hd|4k|mv|clip|premiere)[^)\]]*[\)\]]',
            '', clean_title, flags=re.I,
        ).strip()
        clean_title = re.sub(r'\s*#\w+', '', clean_title).strip()

        out.append({
            "title": clean_title or title,
            "artist": artist,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "source": "youtube",
            "type": "ytdlp",
            "duration": duration,
        })
        if len(out) >= max_results:
            break
    return out


async def search_music_youtube_fast(query, max_results=15):
    """YouTube qidiruv sahifasini o'qish — ~1s (yt-dlp ytsearch 5-15s o'rniga)"""
    from utils.video_downloader import get_http_client, WARP_PROXY
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    params = {"search_query": query, "sp": "EgIQAQ%3D%3D"}  # sp = faqat videolar

    try:
        resp = await get_http_client().get(
            "https://www.youtube.com/results", params=params, headers=headers, timeout=12,
        )
        if resp.status_code == 200:
            results = _parse_yt_search_html(resp.text, max_results)
            if results:
                return results
    except Exception as e:
        logger.info(f"[YT fast] to'g'ridan-to'g'ri xato: {e}")

    # Server IP bloklangan bo'lsa — WARP proxy orqali
    try:
        async with httpx.AsyncClient(
            proxy=WARP_PROXY, follow_redirects=True, timeout=20, verify=False
        ) as client:
            resp = await client.get(
                "https://www.youtube.com/results", params=params, headers=headers
            )
            if resp.status_code == 200:
                return _parse_yt_search_html(resp.text, max_results)
    except Exception as e:
        logger.warning(f"[YT fast] WARP orqali ham xato: {e}")
    return []


_ytmusic = None


async def search_music_ytmusic(query, max_results=20):
    """YouTube Music ichki API — <1s qidiruv, faqat qo'shiqlar, toza metadata"""
    def _search():
        global _ytmusic
        from ytmusicapi import YTMusic
        if _ytmusic is None:
            _ytmusic = YTMusic()
        out = []
        for item in _ytmusic.search(query, filter="songs", limit=max_results):
            vid = item.get("videoId")
            title = item.get("title", "")
            if not vid or not title:
                continue
            duration = item.get("duration_seconds") or 0
            if duration and int(duration) > 900:
                continue
            artists = ", ".join(
                a.get("name", "") for a in (item.get("artists") or []) if a.get("name")
            )
            out.append({
                "title": title,
                "artist": artists or "YouTube Music",
                "url": f"https://www.youtube.com/watch?v={vid}",
                "source": "ytmusic",
                "type": "ytdlp",
                "duration": int(duration),
            })
        return out

    try:
        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(DOWNLOAD_EXECUTOR, _search), timeout=8
        )
    except asyncio.TimeoutError:
        logger.warning(f"YTMusic qidiruv timeout: {query}")
    except Exception as e:
        logger.error(f"YTMusic qidiruvda xatolik: {e}")
    return []


async def search_music_youtube(query, max_results=20):
    """YouTube dan musiqa qidirish — tez va ishonchli"""
    results = []
    try:
        import yt_dlp
        ydl_opts = _get_ydl_opts_search(max_results)

        def _search():
            _results = []
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
                        # Juda uzun videolarni o'tkazib yuborish (15 daqiqadan oshsa)
                        if duration and int(duration) > 900:
                            continue
                        _results.append({
                            "title": title,
                            "artist": uploader or "YouTube",
                            "url": f"https://www.youtube.com/watch?v={video_id}",
                            "source": "youtube",
                            "type": "ytdlp",
                            "duration": int(duration),
                        })
            return _results

        loop = asyncio.get_event_loop()
        results = await asyncio.wait_for(
            loop.run_in_executor(DOWNLOAD_EXECUTOR, _search),
            timeout=15,
        )
    except asyncio.TimeoutError:
        logger.warning(f"YouTube qidiruv timeout: {query}")
    except Exception as e:
        logger.error(f"YouTube qidiruvda xatolik: {e}")
    return results


async def search_music(query):
    """Deezer + YouTube Music parallel qidiruv, Redis cache, deduplikatsiya"""
    # 0. Redis cache — takroriy qidiruvlar bir zumda
    cache_key = f"msearch:{query.lower().strip()}"
    r = loader.redis_client
    if r:
        try:
            cached = await r.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    # 3 manba parallel: Deezer + iTunes (metadata) + YouTube (yuklanadigan)
    deezer_results, itunes_results, youtube_results = await asyncio.gather(
        search_music_deezer(query, max_results=15),
        search_music_itunes(query, max_results=10),
        search_music_youtube_fast(query, max_results=12),
        return_exceptions=True,
    )

    if isinstance(deezer_results, Exception):
        logger.error(f"Deezer qidiruv xatosi: {deezer_results}")
        deezer_results = []
    if isinstance(itunes_results, Exception):
        logger.error(f"iTunes qidiruv xatosi: {itunes_results}")
        itunes_results = []
    if isinstance(youtube_results, Exception):
        logger.error(f"YouTube qidiruv xatosi: {youtube_results}")
        youtube_results = []

    # Birlashtirib deduplikatsiya (Deezer natijalar birinchi)
    combined = list(deezer_results)
    seen_titles = set()
    for item in combined:
        key = f"{item.get('artist', '').lower().strip()} {item.get('title', '').lower().strip()}"
        seen_titles.add(key)

    # iTunes natijalarini qo'shish (Deezer da yo'qlari)
    for item in itunes_results:
        key = f"{item.get('artist', '').lower().strip()} {item.get('title', '').lower().strip()}"
        if key not in seen_titles:
            combined.append(item)
            seen_titles.add(key)

    # Tez YouTube qidiruv ishlamasa — ytmusicapi, u ham bo'lmasa yt-dlp (sekin zaxira)
    if not youtube_results:
        youtube_results = await search_music_ytmusic(query, max_results=10)
    if not youtube_results and not combined:
        youtube_results = await search_music_youtube(query, max_results=10)

    for item in youtube_results:
        key = f"{item.get('artist', '').lower().strip()} {item.get('title', '').lower().strip()}"
        if key not in seen_titles:
            combined.append(item)
            seen_titles.add(key)

    combined = combined[:20]

    # Cache ga yozish (6 soat)
    if r and combined:
        try:
            await r.set(cache_key, json.dumps(combined), ex=6 * 3600)
        except Exception:
            pass

    return combined


# =====================================================
# YouTube dan audio yuklab yuborish (umumiy funksiya)
# =====================================================

async def _download_audio_cobalt(url: str, tmp_dir: str) -> tuple:
    """Cobalt API orqali audio yuklash (tez va ishonchli)"""
    cobalt_url = os.getenv("COBALT_API_URL", "http://cobalt:9000")
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
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
    key = re.sub(r'[^\w\s-]', '', key)
    key = re.sub(r'\s+', ' ', key)
    return f"music:{key}"


async def _music_fail(reason: str):
    alert = await health.record_failure("Music", reason)
    if alert:
        try:
            from handlers.users.health_admin import notify_admins_alert
            await notify_admins_alert(alert)
        except Exception:
            pass


async def download_and_send_audio(chat_id: int, url: str, title_hint: str = "", artist_hint: str = ""):
    """Musiqa yuklab yuborish — cache -> Cobalt -> YouTube (proxysiz -> proxy fallback)"""
    _t0 = time.monotonic()
    caption = "✨ @tinchrobot – Tinchlikni xohlovchilar uchun!"

    # 1. Normalized cache tekshirish (artist+title bo'yicha)
    if title_hint and artist_hint:
        cache_key = _normalize_cache_key(artist_hint, title_hint)
        cached = await cache_db.get_file_id_by_url(cache_key)
        if cached:
            try:
                await bot.send_audio(chat_id=chat_id, audio=cached["file_id"], caption=caption)
                await health.record_cache_hit()
                return True
            except Exception:
                await cache_db.delete_cache_by_url(cache_key)

    # 2. URL bo'yicha cache
    if not url.startswith("deezer:") and not url.startswith("itunes:"):
        cached = await cache_db.get_file_id_by_url(url)
        if cached:
            try:
                await bot.send_audio(chat_id=chat_id, audio=cached["file_id"], caption=caption)
                await health.record_cache_hit()
                return True
            except Exception:
                await cache_db.delete_cache_by_url(url)

    # 3. Deezer URL bo'lsa — YouTube dan qidirish kerak
    # Chart manbasidan to'g'ridan-to'g'ri MP3 havolasi — eng tez yo'l
    if url.startswith("direct:"):
        direct_url = url[len("direct:"):]
        try:
            sent = await bot.send_audio(
                chat_id=chat_id, audio=direct_url, caption=caption,
                title=title_hint[:64] or None,
                performer=artist_hint[:64] or None,
            )
            if sent and sent.audio and title_hint and artist_hint:
                await cache_db.add_cache(
                    "chart", _normalize_cache_key(artist_hint, title_hint),
                    sent.audio.file_id, "audio",
                )
            await health.record_success("Music", time.monotonic() - _t0)
            return True
        except Exception as e:
            logger.info(f"[Music direct] xato, qidiruvga o'tamiz: {e}")
            # Havola ishlamasa — odatdagi qidiruv yo'li bilan davom etamiz
            url = "deezer:0"

    yt_url = url
    if url.startswith("deezer:") or url.startswith("itunes:"):
        search_q = f"{artist_hint} {title_hint}".strip()
        if not search_q:
            return False
        yt_results = await search_music_youtube_fast(search_q, max_results=5)
        if not yt_results:
            yt_results = await search_music_ytmusic(search_q, max_results=5)
        if not yt_results:
            yt_results = await search_music_youtube(search_q, max_results=5)
        if not yt_results:
            await _music_fail(f"YouTube da topilmadi: {search_q}")
            return False
        yt_url = yt_results[0]["url"]

        # YouTube URL bo'yicha ham cache tekshirish
        cached = await cache_db.get_file_id_by_url(yt_url)
        if cached:
            try:
                await bot.send_audio(chat_id=chat_id, audio=cached["file_id"], caption=caption)
                if title_hint and artist_hint:
                    await cache_db.add_cache("youtube", _normalize_cache_key(artist_hint, title_hint), cached["file_id"], "audio")
                return True
            except Exception:
                await cache_db.delete_cache_by_url(yt_url)

    _music_base = os.path.join(SHARED_TEMP_DIR, "music") if SHARED_TEMP_DIR else None
    if _music_base:
        os.makedirs(_music_base, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="ytmusic_", dir=_music_base)
    try:
        info = None
        file_path = None

        # 4. Cobalt API orqali yuklash — YouTube uchun ISHLAMAYDI (cobalt 10.9.4 da
        #    YouTube.js parser buzilgan: PlayerErrorCommand/auth_required). Shuning uchun
        #    faqat YouTube bo'lmagan manbalar uchun urinamiz, aks holda vaqt behuda ketadi.
        is_youtube = ("youtube.com" in yt_url) or ("youtu.be" in yt_url)
        if not is_youtube:
            cobalt_info, cobalt_path = await _download_audio_cobalt(yt_url, tmp_dir)
            if cobalt_info and cobalt_path and os.path.exists(cobalt_path):
                info = cobalt_info
                file_path = cobalt_path
                logger.info(f"[Music] Cobalt orqali yuklandi: {yt_url}")

        # 5. yt-dlp fallback (Cobalt ishlamasa) — semaphore bilan
        if not file_path or not os.path.exists(str(file_path) if file_path else ''):
            async with _download_semaphore:
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

                # WARP proxy ASOSIY yo'l — server IP YouTube bot-tekshiruvida bloklangan
                # (proxysiz so'rov "Sign in to confirm you're not a bot" qaytaradi).
                # 3 marta urinamiz.
                for attempt in range(3):
                    try:
                        info, file_path = await loop.run_in_executor(DOWNLOAD_EXECUTOR, lambda: _yt_download(True))
                        if info and file_path and os.path.exists(file_path):
                            break
                    except Exception as e:
                        logger.info(f"[Music yt-dlp] WARP {attempt+1}-urinish xato: {e}")
                        await asyncio.sleep(1)
                    info, file_path = None, None

                # Proxysiz — oxirgi chora (kamdan-kam ishlaydi, lekin WARP butunlay
                # tushib qolsa zaxira sifatida)
                if not info or not file_path or not os.path.exists(str(file_path) if file_path else ''):
                    try:
                        info, file_path = await loop.run_in_executor(DOWNLOAD_EXECUTOR, lambda: _yt_download(False))
                    except Exception as e:
                        logger.info(f"[Music yt-dlp] proxysiz xato: {e}")
                        info, file_path = None, None

        if not info or not file_path or not os.path.exists(file_path):
            await _music_fail("yt-dlp audio qaytarmadi")
            return False

        title = title_hint or info.get('title', 'Audio')
        artist = artist_hint or info.get('artist') or info.get('uploader', '')
        duration = int(info.get('duration') or 0)
        thumbnail_url = info.get('thumbnail', '')

        # Thumbnail
        thumb_data = None
        if thumbnail_url:
            try:
                from utils.video_downloader import get_http_client
                thumb_resp = await get_http_client().get(thumbnail_url, timeout=5)
                if thumb_resp.status_code == 200:
                    thumb_path = os.path.join(tmp_dir, "thumb.jpg")
                    with open(thumb_path, "wb") as f:
                        f.write(thumb_resp.content)
                    thumb_data = thumb_path
            except Exception:
                pass

        file_size = os.path.getsize(file_path)
        # Local Bot API bilan 2GB, cloud API bilan 50MB
        _limit = (2 * 1024 * 1024 * 1024) if os.getenv("BOT_API_URL") else (50 * 1024 * 1024)
        if file_size > _limit:
            logger.warning(f"[Music] fayl juda katta: {file_size // (1024*1024)}MB")
            return False

        # Fayl nomini chiroyli qilish (Telegram player shu nomni ko'rsatadi)
        _, ext = os.path.splitext(file_path)
        file_ext = ext.lstrip('.') or 'm4a'
        safe_name = re.sub(r'[\\/:*?"<>|]', '', f"{artist} - {title}")[:80] or "audio"
        named_path = os.path.join(tmp_dir, f"{safe_name}.{file_ext}")
        if named_path != file_path:
            try:
                os.rename(file_path, named_path)
                file_path = named_path
            except Exception:
                pass

        kwargs = {
            "chat_id": chat_id,
            "audio": sendable(file_path),
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

        await health.record_success("Music", time.monotonic() - _t0)
        return True
    except Exception as e:
        logger.error(f"Audio yuklash xatosi: {e}", exc_info=True)
        await _music_fail(str(e))
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
    yt_results = await search_music_youtube_fast(search_query, max_results=3)

    markup = None
    if yt_results:
        await state_store.set_state(f"shazam:{chat_id}", {
            'url': yt_results[0]['url'],
            'title': result['title'],
            'artist': result['artist'],
        }, ttl=_SHAZAM_TTL)
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
            src_file_id = message.voice.file_id
        elif message.audio:
            src_file_id = message.audio.file_id
        elif message.video_note:
            src_file_id = message.video_note.file_id
        else:
            await status_msg.edit_text("Audio fayl topilmadi.")
            return

        file_ext = '.ogg'
        if message.audio and message.audio.file_name:
            _, ext = os.path.splitext(message.audio.file_name)
            if ext:
                file_ext = ext

        audio_path = os.path.join(tmp_dir, f"input{file_ext}")
        if not await download_tg_file(src_file_id, audio_path):
            await status_msg.edit_text("⚠️ Faylni yuklab bo'lmadi.")
            return

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
    data = await state_store.pop_state(f"shazam:{chat_id}")

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
        _vid_limit = (2 * 1024 * 1024 * 1024) if os.getenv("BOT_API_URL") else (20 * 1024 * 1024)
        if video.file_size and video.file_size > _vid_limit:
            await status_msg.edit_text("⚠️ Video juda katta.")
            return

        video_path = os.path.join(tmp_dir, "video.mp4")
        if not await download_tg_file(video.file_id, video_path):
            await status_msg.edit_text("⚠️ Videoni yuklab bo'lmadi.")
            return

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

    if len(search_query) > 200:
        await message.reply("⚠️ Qidiruv so'zi juda uzun. 200 belgigacha kiriting.")
        return

    status_msg = await message.reply("🔍 Qidirilmoqda...")

    try:
        all_results = await asyncio.wait_for(search_music(search_query), timeout=30)
    except asyncio.TimeoutError:
        await status_msg.edit_text("⏳ Qidiruv uzoq davom etdi. Qayta urinib ko'ring.")
        return

    if all_results:
        await state_store.set_state(f"res:{message.chat.id}", {
            "results": all_results,
            "current_page": 1,
            "query": search_query,
        }, ttl=_RESULTS_TTL)
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
    data = await state_store.get_state(f"res:{chat_id}")
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
    data["message_id"] = sent_message.message_id
    await state_store.set_state(f"res:{chat_id}", data, ttl=_RESULTS_TTL)


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
        user_data = await state_store.get_state(f"res:{chat_id}")
        if user_data:
            user_data["current_page"] = page
            await state_store.set_state(f"res:{chat_id}", user_data, ttl=_RESULTS_TTL)
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
        user_data = await state_store.pop_state(f"res:{chat_id}")
        if user_data:
            msg_id = user_data.get("message_id")
            if msg_id:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except Exception:
                    pass
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

        # Rate limit tekshirish
        user_id = callback_query.from_user.id
        if not _check_rate_limit(user_id):
            await callback_query.answer(
                f"⏳ Juda tez! {_USER_RATE_WINDOW} sekund ichida {_USER_RATE_LIMIT} ta yuklay olasiz.",
                show_alert=True,
            )
            return

        user_data = await state_store.get_state(f"res:{chat_id}")
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
