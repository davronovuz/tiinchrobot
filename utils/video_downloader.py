import os
import asyncio
import tempfile
import logging
import time
import httpx

logger = logging.getLogger(__name__)

MAX_CONCURRENT_DOWNLOADS = 8
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

TEMP_DIR = tempfile.mkdtemp(prefix="tiinchbot_")

# Cobalt API (Docker ichida ishlaydi)
COBALT_API_URL = os.getenv("COBALT_API_URL", "http://cobalt:9000")

# Cookies fayllari
COOKIES_FILE = os.getenv("COOKIES_FILE", "/app/cookies.txt")

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
    Video yuklab olish (3 bosqichli fallback tizim).
    1-usul: Cobalt API (o'z serverimiz, barcha platformalar)
    2-usul: Platformaga xos fallback (TikTok - tikwm, Instagram - API)
    3-usul: yt-dlp (universal fallback)
    """
    async with download_semaphore:
        platform = get_platform_from_url(url)
        start_time = time.monotonic()

        # 1. Cobalt API orqali
        result = await _download_cobalt(url)
        if result:
            elapsed = time.monotonic() - start_time
            logger.info(f"Cobalt orqali yuklandi: {platform} ({elapsed:.1f}s)")
            return result

        logger.info(f"Cobalt muvaffaqiyatsiz, fallback ishlatiladi: {platform}")

        # 2. Platformaga xos fallback
        if platform == "TikTok":
            result = await _download_tiktok(url)
            if result:
                logger.info("tikwm orqali yuklandi: TikTok")
                return result

        if platform == "Instagram":
            result = await _download_instagram_api(url)
            if result:
                logger.info("Instagram API fallback orqali yuklandi")
                return result

        # 3. yt-dlp universal fallback
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, _download_with_ytdlp, url
            )
            if result:
                elapsed = time.monotonic() - start_time
                logger.info(f"yt-dlp orqali yuklandi: {platform} ({elapsed:.1f}s)")
                return result
        except Exception as e:
            logger.error(f"yt-dlp fallback xatosi: {e}", exc_info=True)

        elapsed = time.monotonic() - start_time
        logger.warning(f"Barcha usullar muvaffaqiyatsiz: {platform} - {url} ({elapsed:.1f}s)")
        return None


async def _download_cobalt(url: str) -> dict:
    """Cobalt API orqali video yuklab olish (o'z serverimiz)"""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            # Cobalt API ga so'rov
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
                logger.warning(f"Cobalt API xatosi: {resp.status_code} {resp.text[:200]}")
                return None

            data = resp.json()
            status = data.get("status")

            if status == "error":
                error_info = data.get("error", {})
                logger.warning(f"Cobalt error: {error_info.get('code', 'unknown')}")
                return None

            # Video URL ni olish
            download_url = None
            if status in ("tunnel", "redirect"):
                download_url = data.get("url")
            elif status == "picker":
                # Bir nechta media — birinchi video ni olamiz
                items = data.get("picker", [])
                if items:
                    download_url = items[0].get("url")

            if not download_url:
                logger.warning(f"Cobalt: download URL topilmadi, status={status}")
                return None

            # Faylni streaming orqali yuklab olish (xotirani tejash)
            platform = get_platform_from_url(url)
            file_ext = ".mp4"
            file_path = os.path.join(TEMP_DIR, f"cobalt_{hash(url) & 0xFFFFFFFF}{file_ext}")

            async with client.stream(
                "GET",
                download_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=600,
            ) as stream:
                if stream.status_code != 200:
                    logger.error(f"Cobalt faylni yuklab olishda xatolik: {stream.status_code}")
                    return None

                with open(file_path, "wb") as f:
                    async for chunk in stream.aiter_bytes(chunk_size=1024 * 64):
                        f.write(chunk)

            filesize = os.path.getsize(file_path)
            if filesize < 1000:  # 1KB dan kichik bo'lsa, xato
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

    except httpx.ConnectError as e:
        logger.warning(f"Cobalt ulanish xatosi (server ishlamayapti?): {e}")
        return None
    except httpx.TimeoutException as e:
        logger.warning(f"Cobalt timeout: {e}")
        return None
    except Exception as e:
        logger.error(f"Cobalt yuklab olishda xatolik: {e}")
        return None


async def _download_tiktok(url: str) -> dict:
    """TikTok — tikwm.com API orqali (fallback)"""
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

            async with client.stream("GET", video_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=120) as stream:
                if stream.status_code != 200:
                    return None
                with open(file_path, "wb") as f:
                    async for chunk in stream.aiter_bytes(chunk_size=1024 * 64):
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
        logger.error(f"TikTok yuklab olishda xatolik: {e}")
    return None


async def _download_instagram_api(url: str) -> dict:
    """Instagram — tashqi API orqali (fallback)"""
    # Bir nechta Instagram API servislarni sinab ko'ramiz
    apis = [
        _try_instagram_api_v1,
        _try_instagram_api_v2,
    ]
    for api_func in apis:
        try:
            result = await api_func(url)
            if result:
                return result
        except Exception as e:
            logger.error(f"Instagram API fallback xatosi: {e}")
    return None


async def _try_instagram_api_v1(url: str) -> dict:
    """Instagram API - saveig usuli"""
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

            # HTML dan download linkni olish
            html_content = data.get("data", "")
            if not html_content:
                return None

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, "lxml")

            # Video URL ni qidirish
            download_link = None
            for a_tag in soup.find_all("a", href=True):
                href = a_tag.get("href", "")
                if href and ("download" in a_tag.text.lower() or ".mp4" in href or "video" in href.lower()):
                    download_link = href
                    break

            # Agar video topilmasa, birinchi linkni olamiz
            if not download_link:
                links = soup.find_all("a", href=True)
                for link in links:
                    href = link.get("href", "")
                    if href.startswith("http"):
                        download_link = href
                        break

            if not download_link:
                return None

            return await _download_from_url(download_link, "Instagram", url)

    except Exception as e:
        logger.error(f"Instagram API v1 xatosi: {e}")
    return None


async def _try_instagram_api_v2(url: str) -> dict:
    """Instagram API - fastdl usuli"""
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
            # Video URL ni qidirish
            media_list = data.get("url", [])
            if isinstance(media_list, str):
                media_list = [{"url": media_list}]
            elif isinstance(media_list, list) and media_list:
                pass
            else:
                return None

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

            return await _download_from_url(video_url, "Instagram", url)

    except Exception as e:
        logger.error(f"Instagram API v2 xatosi: {e}")
    return None


async def _download_from_url(download_url: str, platform: str, original_url: str) -> dict:
    """URL dan faylni streaming orqali yuklab olish"""
    try:
        file_path = os.path.join(TEMP_DIR, f"{platform.lower()}_{hash(original_url) & 0xFFFFFFFF}.mp4")

        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            async with client.stream(
                "GET",
                download_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                },
            ) as stream:
                if stream.status_code != 200:
                    return None
                with open(file_path, "wb") as f:
                    async for chunk in stream.aiter_bytes(chunk_size=1024 * 64):
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
        logger.error(f"URL dan yuklab olishda xatolik: {e}")
    return None


def _download_with_ytdlp(url: str) -> dict:
    """yt-dlp orqali yuklash (universal fallback)"""
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

    # YouTube uchun maxsus sozlamalar (2026 mart uchun yangilangan)
    if platform == "YouTube":
        ydl_opts['extractor_args'] = {
            'youtube': {
                # android eskirgan, web + web_safari + android_vr ishlatamiz
                'player_client': ['web', 'web_safari', 'android_vr'],
                # webpage ni skip qilMAymiz — yangi yt-dlp o'zi optimallashtiradi
                'player_skip': ['configs'],
            }
        }

    # Instagram uchun cookies shart
    if platform == "Instagram":
        if not os.path.exists(COOKIES_FILE):
            logger.warning("Instagram uchun cookies.txt kerak, lekin fayl topilmadi")

    # Cookies fayli mavjud bo'lsa ishlatamiz
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    # curl_cffi mavjud bo'lsa brauzer impersonatsiyasi
    try:
        import curl_cffi  # noqa: F401
        ydl_opts['impersonate'] = 'chrome'
    except ImportError:
        pass

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return None

            file_path = ydl.prepare_filename(info)
            # merge_output_format mp4 ni hisobga olamiz
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
        logger.error(f"yt-dlp xatosi ({platform}): {e}", exc_info=True)
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
