import os
import asyncio
import tempfile
import logging
import time
import hashlib
import re
import json
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
            # Stories uchun maxsus handler
            if "/stories/" in url:
                result = await _download_instagram_stories(url)
                if result:
                    logger.info(f"[Instagram Stories] yuklandi")
                    return result
            else:
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
    """Instagram — Instagram v1 API orqali yuklab olish (sessionid cookies bilan)"""
    # URL dan shortcode olish
    shortcode = _extract_instagram_shortcode(url)
    if not shortcode:
        return None

    result = await _try_instagram_v1_api(shortcode, url)
    if result:
        return result
    return None


def _extract_instagram_shortcode(url: str) -> str:
    """Instagram URL dan shortcode olish"""
    patterns = [
        r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _get_instagram_cookies() -> dict:
    """cookies.txt dan Instagram cookies ni dict sifatida olish"""
    cookies = {}
    if not os.path.exists(COOKIES_FILE):
        return cookies
    try:
        with open(COOKIES_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if ".instagram.com" in line and not line.startswith("#"):
                    parts = line.split("\t")
                    if len(parts) >= 7:
                        cookies[parts[5]] = parts[6]
    except Exception:
        pass
    return cookies


async def _try_instagram_v1_api(shortcode: str, original_url: str) -> dict:
    """Instagram v1 API — sessionid bilan media ma'lumotlarini olish"""
    cookies = _get_instagram_cookies()
    if not cookies.get("sessionid"):
        logger.warning("[Instagram v1] sessionid yo'q")
        return None

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, cookies=cookies) as client:
            # v1 API - media info
            resp = await client.get(
                f"https://i.instagram.com/api/v1/media/{shortcode}/info/",
                headers={
                    "User-Agent": "Instagram 275.0.0.27.98 Android (33/13; 440dpi; 1080x2400; samsung; SM-A536B; a53x; exynos1280; en_US; 458229258)",
                    "X-IG-App-ID": "567067343352427",
                }
            )

            if resp.status_code != 200:
                logger.warning(f"[Instagram v1] HTTP {resp.status_code}")
                return None

            data = resp.json()
            items = data.get("items", [])
            if not items:
                return None

            item = items[0]
            media_type = item.get("media_type")

            # Video
            video_versions = item.get("video_versions", [])
            if video_versions:
                video_url = video_versions[0].get("url")
                if video_url:
                    return await _stream_download(video_url, "Instagram", original_url)

            # Carousel (bir nechta rasm/video)
            carousel = item.get("carousel_media", [])
            if carousel:
                for cm in carousel:
                    vv = cm.get("video_versions", [])
                    if vv:
                        return await _stream_download(vv[0]["url"], "Instagram", original_url)
                # Agar hammasi rasm bo'lsa, birinchi rasmni yuklash
                img = carousel[0].get("image_versions2", {}).get("candidates", [])
                if img:
                    return await _stream_download(img[0]["url"], "Instagram", original_url)

            # Rasm
            img_versions = item.get("image_versions2", {}).get("candidates", [])
            if img_versions:
                return await _stream_download(img_versions[0]["url"], "Instagram", original_url)

    except Exception as e:
        logger.error(f"[Instagram v1] xatolik: {e}")
    return None


async def _download_instagram_stories(url: str) -> dict:
    """Instagram Stories yuklab olish — v1 API orqali"""
    cookies = _get_instagram_cookies()
    if not cookies.get("sessionid"):
        logger.warning("[Stories] sessionid yo'q")
        return None

    # URL dan username olish
    m = re.search(r"instagram\.com/stories/([^/?]+)", url)
    if not m:
        return None
    username = m.group(1)

    # URL da story ID bormi?
    story_id_match = re.search(r"instagram\.com/stories/[^/]+/(\d+)", url)
    target_story_id = story_id_match.group(1) if story_id_match else None

    ig_headers = {
        "User-Agent": "Instagram 275.0.0.27.98 Android (33/13; 440dpi; 1080x2400; samsung; SM-A536B; a53x; exynos1280; en_US; 458229258)",
        "X-IG-App-ID": "567067343352427",
    }

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, cookies=cookies) as client:
            # 1. Username dan user_id olish
            resp = await client.get(
                f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}",
                headers=ig_headers,
            )
            if resp.status_code != 200:
                logger.warning(f"[Stories] user info HTTP {resp.status_code}")
                return None

            user_data = resp.json()
            user_id = user_data.get("data", {}).get("user", {}).get("id")
            if not user_id:
                return None

            # 2. Stories olish
            resp2 = await client.get(
                f"https://i.instagram.com/api/v1/feed/reels_media/?reel_ids={user_id}",
                headers=ig_headers,
            )
            if resp2.status_code != 200:
                logger.warning(f"[Stories] reels_media HTTP {resp2.status_code}")
                return None

            stories_data = resp2.json()
            reels = stories_data.get("reels", {})
            reel = reels.get(str(user_id), {})
            story_items = reel.get("items", [])

            if not story_items:
                logger.info(f"[Stories] {username} da stories yo'q")
                return None

            # Agar aniq story_id berilgan bo'lsa, shuni topamiz
            if target_story_id:
                for item in story_items:
                    if str(item.get("pk")) == target_story_id or str(item.get("id", "")).startswith(target_story_id):
                        return await _download_story_item(item, url)
                # Topilmasa birinchisini olish
                logger.info(f"[Stories] story_id {target_story_id} topilmadi, oxirgi story yuklanadi")

            # Eng oxirgi story ni yuklash
            item = story_items[-1]
            return await _download_story_item(item, url)

    except Exception as e:
        logger.error(f"[Stories] xatolik: {e}", exc_info=True)
    return None


async def _download_story_item(item: dict, original_url: str) -> dict:
    """Bitta story itemni yuklab olish"""
    media_type = item.get("media_type")

    # Video story
    video_versions = item.get("video_versions", [])
    if video_versions:
        video_url = video_versions[0].get("url")
        if video_url:
            return await _stream_download(video_url, "Instagram", original_url)

    # Rasm story
    img_versions = item.get("image_versions2", {}).get("candidates", [])
    if img_versions:
        img_url = img_versions[0].get("url")
        if img_url:
            # Rasmni yuklab olish
            file_path = os.path.join(TEMP_DIR, f"story_{hash(original_url) & 0xFFFFFFFF}.jpg")
            try:
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                    async with client.stream("GET", img_url, headers={
                        "User-Agent": "Mozilla/5.0"
                    }) as stream:
                        if stream.status_code != 200:
                            return None
                        with open(file_path, "wb") as f:
                            async for chunk in stream.aiter_bytes(chunk_size=65536):
                                f.write(chunk)

                filesize = os.path.getsize(file_path)
                if filesize < 500:
                    os.unlink(file_path)
                    return None

                return {
                    'file_path': file_path,
                    'title': 'Instagram Story',
                    'duration': 0,
                    'filesize': filesize,
                    'thumbnail': '',
                    'platform': 'Instagram',
                    'width': 0,
                    'height': 0,
                    'is_photo': True,
                }
            except Exception as e:
                logger.error(f"[Story img] xatolik: {e}")
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
                'player_client': ['web'],
                'player_skip': [],
            },
            # bgutil PO token provider — avtomatik token (Docker: http://bgutil:4416)
            'youtubepot-bgutilhttp': {
                'base_url': [BGUTIL_URL],
            },
        }

    # Cookies (YouTube login + Instagram sessionid)
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

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
                'player_client': ['web'],
                'player_skip': [],
            },
            'youtubepot-bgutilhttp': {
                'base_url': [BGUTIL_URL],
            },
        }

    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

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
                'player_client': ['web'],
                'player_skip': [],
            },
            'youtubepot-bgutilhttp': {
                'base_url': [BGUTIL_URL],
            },
        },
    }
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

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
                'player_client': ['web'],
                'player_skip': [],
            },
            'youtubepot-bgutilhttp': {
                'base_url': [BGUTIL_URL],
            },
        },
    }
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

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
