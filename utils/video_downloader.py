import os
import asyncio
import tempfile
import logging
import time
import hashlib
import httpx

logger = logging.getLogger(__name__)

MAX_CONCURRENT_DOWNLOADS = 8
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

TEMP_DIR = tempfile.mkdtemp(prefix="tiinchbot_")

# YouTube URL va format ma'lumotlarini vaqtincha saqlash
# {url_hash: {"url": "...", "formats": [...], "timestamp": ...}}
_yt_format_cache = {}

# Cobalt API (Docker ichida ishlaydi)
COBALT_API_URL = os.getenv("COBALT_API_URL", "http://cobalt:9000")

# Cookies fayli (yt-dlp uchun Netscape format)
COOKIES_FILE = os.getenv("COOKIES_FILE", "/app/cookies.txt")

# bgutil PO Token provider (YouTube uchun avtomatik token)
BGUTIL_URL = os.getenv("BGUTIL_URL", "http://bgutil:4416")

SUPPORTED_PLATFORMS = {
    "instagram.com": "Instagram",
    "tiktok.com": "TikTok",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "youtube.com/shorts": "YouTube",
    "music.youtube.com": "YouTube",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "twitter.com": "Twitter",
    "x.com": "Twitter",
    "vimeo.com": "Vimeo",
    "dailymotion.com": "Dailymotion",
    "reddit.com": "Reddit",
    "pinterest.com": "Pinterest",
    "snapchat.com": "Snapchat",
    "likee.video": "Likee",
    "kwai.com": "Kwai",
}


def get_platform_from_url(url: str) -> str:
    lower_url = url.lower()
    for keyword, platform_name in SUPPORTED_PLATFORMS.items():
        if keyword in lower_url:
            return platform_name
    return "Unknown"


def is_supported_url(url: str) -> bool:
    lower_url = url.lower()
    return any(keyword in lower_url for keyword in SUPPORTED_PLATFORMS)


async def download_video(url: str) -> dict:
    """
    Video yuklab olish — production fallback tizim:
    1. Cobalt API (PO token + WARP proxy — YouTube, Instagram, TikTok, Twitter)
    2. Platformaga xos API (TikTok - tikwm, Instagram - saveig/fastdl)
    3. yt-dlp + bgutil PO token (universal fallback)
    """
    async with download_semaphore:
        platform = get_platform_from_url(url)
        start_time = time.monotonic()

        # 1. Cobalt API — asosiy (WARP proxy + PO token + cookies)
        result = await _download_cobalt(url)
        if result:
            elapsed = time.monotonic() - start_time
            logger.info(f"[Cobalt] {platform} yuklandi ({elapsed:.1f}s)")
            return result

        logger.info(f"[Cobalt] muvaffaqiyatsiz: {platform}, fallback...")

        # 2. Platformaga xos fallback
        if platform == "TikTok":
            result = await _download_tiktok(url)
            if result:
                logger.info(f"[tikwm] TikTok yuklandi")
                return result

        if platform == "Instagram":
            result = await _download_instagram_api(url)
            if result:
                logger.info(f"[Instagram API] yuklandi")
                return result

        # 3. yt-dlp + bgutil PO token — universal fallback
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, _download_with_ytdlp, url
            )
            if result:
                elapsed = time.monotonic() - start_time
                logger.info(f"[yt-dlp] {platform} yuklandi ({elapsed:.1f}s)")
                return result
        except Exception as e:
            logger.error(f"[yt-dlp] xatosi: {e}", exc_info=True)

        elapsed = time.monotonic() - start_time
        logger.warning(f"BARCHA USULLAR MUVAFFAQIYATSIZ: {platform} - {url} ({elapsed:.1f}s)")
        return None


async def _download_cobalt(url: str) -> dict:
    """
    Cobalt API v10 orqali video yuklab olish.
    YouTube: PO token (YOUTUBE_GENERATE_PO_TOKENS=1) + WARP proxy
    Instagram: cookies.json dan session
    """
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.post(
                COBALT_API_URL,
                json={
                    "url": url,
                    "videoQuality": "1080",
                    "filenameStyle": "basic",
                    "youtubeVideoCodec": "h264",
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                }
            )

            if resp.status_code != 200:
                logger.warning(f"[Cobalt] HTTP {resp.status_code}: {resp.text[:200]}")
                return None

            data = resp.json()
            status = data.get("status")

            if status == "error":
                error_info = data.get("error", {})
                logger.warning(f"[Cobalt] error: {error_info.get('code', 'unknown')}")
                return None

            # Video URL ni olish
            download_url = None
            if status in ("tunnel", "redirect"):
                download_url = data.get("url")
            elif status == "picker":
                items = data.get("picker", [])
                if items:
                    download_url = items[0].get("url")

            if not download_url:
                logger.warning(f"[Cobalt] URL topilmadi, status={status}")
                return None

            # Streaming yuklab olish (katta fayllar uchun xotirani tejash)
            platform = get_platform_from_url(url)
            file_path = os.path.join(TEMP_DIR, f"cobalt_{hash(url) & 0xFFFFFFFF}.mp4")

            async with client.stream(
                "GET", download_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=600,
            ) as stream:
                if stream.status_code != 200:
                    logger.error(f"[Cobalt] fayl yuklab olish: HTTP {stream.status_code}")
                    return None

                with open(file_path, "wb") as f:
                    async for chunk in stream.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

            filesize = os.path.getsize(file_path)
            if filesize < 1000:
                os.unlink(file_path)
                return None

            return {
                'file_path': file_path,
                'title': data.get('filename', f'{platform} Video'),
                'duration': 0,
                'filesize': filesize,
                'thumbnail': '',
                'platform': platform,
                'width': 0,
                'height': 0,
            }

    except httpx.ConnectError:
        logger.warning("[Cobalt] server ishlamayapti")
        return None
    except httpx.TimeoutException:
        logger.warning("[Cobalt] timeout")
        return None
    except Exception as e:
        logger.error(f"[Cobalt] xatolik: {e}")
        return None


async def _download_tiktok(url: str) -> dict:
    """TikTok — tikwm.com API (watermark siz, HD)"""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                "https://www.tikwm.com/api/",
                params={"url": url, "hd": 1},
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            if data.get("code") != 0 or not data.get("data"):
                return None

            video_data = data["data"]
            video_url = video_data.get("hdplay") or video_data.get("play")
            if not video_url:
                return None

            file_path = os.path.join(TEMP_DIR, f"tiktok_{video_data.get('id', 'video')}.mp4")

            async with client.stream(
                "GET", video_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=120,
            ) as stream:
                if stream.status_code != 200:
                    return None
                with open(file_path, "wb") as f:
                    async for chunk in stream.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

            return {
                'file_path': file_path,
                'title': (video_data.get("title") or "TikTok Video")[:100],
                'duration': video_data.get("duration", 0),
                'filesize': os.path.getsize(file_path),
                'thumbnail': video_data.get("cover", ""),
                'platform': "TikTok",
                'width': video_data.get("width", 0),
                'height': video_data.get("height", 0),
            }
    except Exception as e:
        logger.error(f"[tikwm] xatolik: {e}")
    return None


async def _download_instagram_api(url: str) -> dict:
    """Instagram — tashqi API fallback zanjiri"""
    apis = [_try_instagram_saveig, _try_instagram_fastdl]
    for api_func in apis:
        try:
            result = await api_func(url)
            if result:
                return result
        except Exception as e:
            logger.error(f"[Instagram] {api_func.__name__} xatosi: {e}")
    return None


async def _try_instagram_saveig(url: str) -> dict:
    """saveig.app API"""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.post(
                "https://v3.saveig.app/api/ajaxSearch",
                data={"q": url, "t": "media", "lang": "en"},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Origin": "https://saveig.app",
                    "Referer": "https://saveig.app/",
                }
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            if data.get("status") != "ok":
                return None

            html_content = data.get("data", "")
            if not html_content:
                return None

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, "lxml")

            download_link = None
            for a_tag in soup.find_all("a", href=True):
                href = a_tag.get("href", "")
                if href and ("download" in a_tag.text.lower() or ".mp4" in href or "video" in href.lower()):
                    download_link = href
                    break

            if not download_link:
                for link in soup.find_all("a", href=True):
                    href = link.get("href", "")
                    if href.startswith("http"):
                        download_link = href
                        break

            if not download_link:
                return None

            return await _stream_download(download_link, "Instagram", url)
    except Exception as e:
        logger.error(f"[saveig] xatolik: {e}")
    return None


async def _try_instagram_fastdl(url: str) -> dict:
    """fastdl.app API"""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.post(
                "https://fastdl.app/api/convert",
                json={"url": url},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Origin": "https://fastdl.app",
                    "Referer": "https://fastdl.app/",
                    "Content-Type": "application/json",
                }
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            media_list = data.get("url", [])
            if isinstance(media_list, str):
                media_list = [media_list]

            video_url = None
            for item in media_list:
                if isinstance(item, dict):
                    video_url = item.get("url")
                elif isinstance(item, str):
                    video_url = item
                if video_url:
                    break

            if not video_url:
                return None

            return await _stream_download(video_url, "Instagram", url)
    except Exception as e:
        logger.error(f"[fastdl] xatolik: {e}")
    return None


async def _stream_download(download_url: str, platform: str, original_url: str) -> dict:
    """URL dan faylni streaming yuklab olish"""
    try:
        file_path = os.path.join(TEMP_DIR, f"{platform.lower()}_{hash(original_url) & 0xFFFFFFFF}.mp4")

        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            async with client.stream(
                "GET", download_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                },
            ) as stream:
                if stream.status_code != 200:
                    return None
                with open(file_path, "wb") as f:
                    async for chunk in stream.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

        filesize = os.path.getsize(file_path)
        if filesize < 1000:
            os.unlink(file_path)
            return None

        return {
            'file_path': file_path,
            'title': f'{platform} Video',
            'duration': 0,
            'filesize': filesize,
            'thumbnail': '',
            'platform': platform,
            'width': 0,
            'height': 0,
        }
    except Exception as e:
        logger.error(f"[stream_download] xatolik: {e}")
    return None


def _download_with_ytdlp(url: str) -> dict:
    """
    yt-dlp + bgutil PO token provider — universal fallback.
    bgutil plugin avtomatik PO token generatsiya qiladi (cookies kerak emas).
    """
    import yt_dlp
    output_template = os.path.join(TEMP_DIR, "%(id)s.%(ext)s")
    platform = get_platform_from_url(url)

    ydl_opts = {
        'outtmpl': output_template,
        'format': 'bestvideo[height<=1080][filesize<2G]+bestaudio/best[height<=1080][filesize<2G]/best',
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 10,
        'fragment_retries': 10,
        'file_access_retries': 5,
        'extractor_retries': 5,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        },
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }],
    }

    # YouTube: bgutil PO token + to'g'ri player_client
    if platform == "YouTube":
        ydl_opts['extractor_args'] = {
            'youtube': {
                'player_client': ['web', 'web_safari', 'android_vr'],
                'player_skip': ['configs'],
            },
            # bgutil PO token provider — avtomatik token (Docker: http://bgutil:4416)
            'youtubepot-bgutilhttp': {
                'base_url': [BGUTIL_URL],
            },
        }

    # Cookies mavjud bo'lsa ishlatamiz (Instagram uchun muhim)
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    # curl_cffi brauzer impersonatsiyasi (TLS fingerprint)
    try:
        import curl_cffi  # noqa: F401
        from yt_dlp.networking.impersonate import ImpersonateTarget
        ydl_opts['impersonate'] = ImpersonateTarget('chrome', '131', 'macos', '14')
    except (ImportError, Exception):
        pass

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return None

            file_path = ydl.prepare_filename(info)
            if not os.path.exists(file_path):
                base, _ = os.path.splitext(file_path)
                for ext in ['.mp4', '.mkv', '.webm', '.mp3', '.m4a']:
                    candidate = base + ext
                    if os.path.exists(candidate):
                        file_path = candidate
                        break

            if not os.path.exists(file_path):
                return None

            return {
                'file_path': file_path,
                'title': info.get('title', 'Video'),
                'duration': info.get('duration', 0),
                'filesize': os.path.getsize(file_path),
                'thumbnail': info.get('thumbnail', ''),
                'platform': platform,
                'width': info.get('width', 0),
                'height': info.get('height', 0),
            }
    except Exception as e:
        logger.error(f"[yt-dlp] {platform} xatosi: {e}", exc_info=True)
        return None


def cleanup_file(file_path: str):
    try:
        if file_path and os.path.exists(file_path):
            os.unlink(file_path)
    except Exception as e:
        logger.error(f"Faylni o'chirishda xatolik: {e}")


def cleanup_temp_dir():
    try:
        for f in os.listdir(TEMP_DIR):
            filepath = os.path.join(TEMP_DIR, f)
            if os.path.isfile(filepath):
                os.unlink(filepath)
    except Exception as e:
        logger.error(f"Temp papka tozalashda xatolik: {e}")


# ==================== YouTube Format/Sifat tanlash ====================

def make_url_hash(url: str) -> str:
    """URL dan qisqa hash yasash (callback_data uchun)"""
    return hashlib.md5(url.encode()).hexdigest()[:10]


def _format_filesize(size_bytes) -> str:
    """Baytni o'qilishi oson formatga o'girish"""
    if not size_bytes:
        return ""
    mb = size_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.1f}GB"
    return f"{mb:.0f}MB"


async def get_youtube_formats(url: str) -> dict:
    """
    YouTube videoning mavjud formatlarini olish.
    Return: {"url_hash": "abc123", "title": "...", "formats": [...], "thumbnail": "..."}
    """
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, _extract_youtube_formats, url
        )
        if result:
            url_hash = make_url_hash(url)
            # Cache ga saqlash (5 daqiqa)
            _yt_format_cache[url_hash] = {
                "url": url,
                "formats": result["formats"],
                "title": result["title"],
                "thumbnail": result.get("thumbnail", ""),
                "duration": result.get("duration", 0),
                "timestamp": time.monotonic(),
            }
            result["url_hash"] = url_hash
            return result
    except Exception as e:
        logger.error(f"[YouTube formats] xatosi: {e}", exc_info=True)
    return None


def _extract_youtube_formats(url: str) -> dict:
    """yt-dlp orqali YouTube formatlarini olish (download qilmaydi)"""
    import yt_dlp

    ydl_opts = {
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 20,
        'skip_download': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        },
    }

    platform = get_platform_from_url(url)
    if platform == "YouTube":
        ydl_opts['extractor_args'] = {
            'youtube': {
                'player_client': ['web', 'web_safari', 'android_vr'],
                'player_skip': ['configs'],
            },
            'youtubepot-bgutilhttp': {
                'base_url': [BGUTIL_URL],
            },
        }

    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    try:
        import curl_cffi  # noqa: F401
        from yt_dlp.networking.impersonate import ImpersonateTarget
        ydl_opts['impersonate'] = ImpersonateTarget('chrome', '131', 'macos', '14')
    except (ImportError, Exception):
        pass

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            return None

        title = info.get('title', 'YouTube Video')
        thumbnail = info.get('thumbnail', '')
        duration = info.get('duration', 0)
        all_formats = info.get('formats', [])

        # Sifatlarni guruhlash
        quality_map = {}  # height -> {"format_id", "filesize", ...}

        for fmt in all_formats:
            height = fmt.get('height')
            vcodec = fmt.get('vcodec', 'none')
            acodec = fmt.get('acodec', 'none')

            # Faqat video formatlar (acodec bo'lishi mumkin yoki yo'q)
            if not height or vcodec == 'none':
                continue

            # Eng yaxshi formatni tanlash (h264 ustunlik)
            ext = fmt.get('ext', 'mp4')
            format_id = fmt.get('format_id', '')
            filesize = fmt.get('filesize') or fmt.get('filesize_approx') or 0
            codec = vcodec.split('.')[0] if vcodec else ''

            # Agar bu sifat allaqachon bor bo'lsa, h264 ni ustun qo'yamiz
            if height in quality_map:
                existing = quality_map[height]
                # h264 (avc1) ni afzal ko'ramiz
                if 'avc' in codec or 'h264' in codec:
                    pass  # yangilash
                elif 'avc' in existing.get('codec', '') or 'h264' in existing.get('codec', ''):
                    continue  # mavjudini saqlab qolish
                elif filesize <= existing.get('filesize', 0):
                    continue

            quality_map[height] = {
                'format_id': format_id,
                'height': height,
                'filesize': filesize,
                'codec': codec,
                'ext': ext,
                'has_audio': acodec != 'none',
            }

        if not quality_map:
            return None

        # Tartiblash (yuqoridan pastga)
        sorted_qualities = sorted(quality_map.keys(), reverse=True)

        # Eng yaxshi audio format ID
        best_audio_id = None
        for fmt in all_formats:
            if fmt.get('acodec', 'none') != 'none' and fmt.get('vcodec', 'none') == 'none':
                if fmt.get('ext') in ('m4a', 'mp4', 'webm'):
                    best_audio_id = fmt.get('format_id')
                    if fmt.get('ext') == 'm4a':
                        break  # m4a eng yaxshi

        # Formatlar ro'yxatini tayyorlash
        formats_list = []
        target_qualities = [2160, 1440, 1080, 720, 480, 360]

        for target_h in target_qualities:
            # Eng yaqin mavjud sifatni topish
            best_match = None
            for h in sorted_qualities:
                if h == target_h:
                    best_match = h
                    break
            if not best_match:
                continue

            fmt_info = quality_map[best_match]
            fid = fmt_info['format_id']

            # Video + audio birlashtirish uchun format
            if not fmt_info['has_audio'] and best_audio_id:
                combined_id = f"{fid}+{best_audio_id}"
            else:
                combined_id = fid

            # Fayl hajmini hisoblash (video + audio taxminiy)
            total_size = fmt_info['filesize']

            formats_list.append({
                'quality': f"{best_match}p",
                'format_id': combined_id,
                'size_text': _format_filesize(total_size) if total_size else "",
                'height': best_match,
            })

        if not formats_list:
            return None

        return {
            'title': title,
            'thumbnail': thumbnail,
            'duration': duration,
            'formats': formats_list,
        }


async def download_youtube_with_format(url_hash: str, format_id: str) -> dict:
    """
    Tanlangan formatda YouTube videoni yuklab olish.
    format_id: "137+140" (video+audio) yoki "audio" (faqat audio)
    """
    cache_entry = _yt_format_cache.get(url_hash)
    if not cache_entry:
        return None

    url = cache_entry["url"]

    # Cache muddati tekshirish (5 daqiqa)
    if time.monotonic() - cache_entry["timestamp"] > 300:
        _yt_format_cache.pop(url_hash, None)
        return None

    async with download_semaphore:
        start_time = time.monotonic()

        if format_id == "audio":
            result = await asyncio.get_event_loop().run_in_executor(
                None, _download_youtube_audio, url
            )
        else:
            result = await asyncio.get_event_loop().run_in_executor(
                None, _download_youtube_format, url, format_id
            )

        if result:
            elapsed = time.monotonic() - start_time
            logger.info(f"[YouTube format] yuklandi: {format_id} ({elapsed:.1f}s)")

        # Cache dan o'chirish
        _yt_format_cache.pop(url_hash, None)
        return result


def _download_youtube_format(url: str, format_id: str) -> dict:
    """Tanlangan formatda YouTube videoni yuklab olish"""
    import yt_dlp

    output_template = os.path.join(TEMP_DIR, "%(id)s.%(ext)s")
    ydl_opts = {
        'outtmpl': output_template,
        'format': f"{format_id}/bestvideo+bestaudio/best",
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 10,
        'fragment_retries': 10,
        'file_access_retries': 5,
        'extractor_retries': 5,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        },
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }],
        'extractor_args': {
            'youtube': {
                'player_client': ['web', 'web_safari', 'android_vr'],
                'player_skip': ['configs'],
            },
            'youtubepot-bgutilhttp': {
                'base_url': [BGUTIL_URL],
            },
        },
    }

    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    try:
        import curl_cffi  # noqa: F401
        from yt_dlp.networking.impersonate import ImpersonateTarget
        ydl_opts['impersonate'] = ImpersonateTarget('chrome', '131', 'macos', '14')
    except (ImportError, Exception):
        pass

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return None

            file_path = ydl.prepare_filename(info)
            if not os.path.exists(file_path):
                base, _ = os.path.splitext(file_path)
                for ext in ['.mp4', '.mkv', '.webm']:
                    candidate = base + ext
                    if os.path.exists(candidate):
                        file_path = candidate
                        break

            if not os.path.exists(file_path):
                return None

            return {
                'file_path': file_path,
                'title': info.get('title', 'YouTube Video'),
                'duration': info.get('duration', 0),
                'filesize': os.path.getsize(file_path),
                'thumbnail': info.get('thumbnail', ''),
                'platform': 'YouTube',
                'width': info.get('width', 0),
                'height': info.get('height', 0),
            }
    except Exception as e:
        logger.error(f"[YouTube format download] xatosi: {e}", exc_info=True)
        return None


def _download_youtube_audio(url: str) -> dict:
    """YouTube dan faqat audio (MP3) yuklab olish"""
    import yt_dlp

    output_template = os.path.join(TEMP_DIR, "%(id)s.%(ext)s")
    ydl_opts = {
        'outtmpl': output_template,
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 10,
        'fragment_retries': 10,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        },
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'extractor_args': {
            'youtube': {
                'player_client': ['web', 'web_safari', 'android_vr'],
                'player_skip': ['configs'],
            },
            'youtubepot-bgutilhttp': {
                'base_url': [BGUTIL_URL],
            },
        },
    }

    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    try:
        import curl_cffi  # noqa: F401
        from yt_dlp.networking.impersonate import ImpersonateTarget
        ydl_opts['impersonate'] = ImpersonateTarget('chrome', '131', 'macos', '14')
    except (ImportError, Exception):
        pass

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return None

            file_path = ydl.prepare_filename(info)
            # Audio postprocessor ext ni o'zgartiradi
            base, _ = os.path.splitext(file_path)
            for ext in ['.mp3', '.m4a', '.opus', '.webm']:
                candidate = base + ext
                if os.path.exists(candidate):
                    file_path = candidate
                    break

            if not os.path.exists(file_path):
                return None

            return {
                'file_path': file_path,
                'title': info.get('title', 'YouTube Audio'),
                'duration': info.get('duration', 0),
                'filesize': os.path.getsize(file_path),
                'thumbnail': info.get('thumbnail', ''),
                'platform': 'YouTube',
                'width': 0,
                'height': 0,
                'is_audio': True,
            }
    except Exception as e:
        logger.error(f"[YouTube audio] xatosi: {e}", exc_info=True)
        return None


def get_cached_yt_url(url_hash: str) -> str:
    """Cache dan URL olish"""
    entry = _yt_format_cache.get(url_hash)
    if entry:
        return entry["url"]
    return None
