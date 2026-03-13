import os
import asyncio
import tempfile
import logging
import yt_dlp
import httpx

logger = logging.getLogger(__name__)

MAX_CONCURRENT_DOWNLOADS = 5
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

TEMP_DIR = tempfile.mkdtemp(prefix="tiinchbot_")

SUPPORTED_PLATFORMS = {
    "instagram.com": "Instagram",
    "tiktok.com": "TikTok",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
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
    Video yuklab olish — platforma ga qarab turli usullar.
    """
    async with download_semaphore:
        platform = get_platform_from_url(url)

        # TikTok — tikwm.com API (eng ishonchli)
        if platform == "TikTok":
            result = await _download_tiktok(url)
            if result:
                return result

        # Instagram — yt-dlp bilan sinab koramiz
        if platform == "Instagram":
            result = await _download_instagram(url)
            if result:
                return result

        # Boshqa platformalar va fallback — yt-dlp
        result = await asyncio.get_event_loop().run_in_executor(
            None, _download_with_ytdlp, url
        )
        return result


async def _download_tiktok(url: str) -> dict:
    """TikTok — tikwm.com API orqali"""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                f"https://www.tikwm.com/api/",
                params={"url": url, "hd": 1},
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if resp.status_code != 200:
                logger.error(f"tikwm API xatosi: {resp.status_code}")
                return None

            data = resp.json()
            if data.get("code") != 0 or not data.get("data"):
                logger.error(f"tikwm javob xatosi: {data.get('msg')}")
                return None

            video_data = data["data"]
            # HD yoki oddiy video URL
            video_url = video_data.get("hdplay") or video_data.get("play")
            if not video_url:
                return None

            # Video ni yuklab olish
            file_path = os.path.join(TEMP_DIR, f"tiktok_{video_data.get('id', 'video')}.mp4")
            resp2 = await client.get(video_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=120)
            if resp2.status_code == 200:
                with open(file_path, "wb") as f:
                    f.write(resp2.content)

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


async def _download_instagram(url: str) -> dict:
    """Instagram — avval yt-dlp, keyin API fallback"""
    # yt-dlp bilan sinab koramiz
    result = await asyncio.get_event_loop().run_in_executor(
        None, _download_with_ytdlp, url
    )
    if result:
        return result

    # Fallback: igdownloader API
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                "https://v3.igdownloader.app/api/ig/post",
                params={"url": url},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                medias = data.get("items", [])
                if medias:
                    media_url = medias[0].get("url", "")
                    if media_url:
                        file_path = os.path.join(TEMP_DIR, f"ig_{hash(url)}.mp4")
                        resp2 = await client.get(media_url, timeout=120)
                        if resp2.status_code == 200:
                            with open(file_path, "wb") as f:
                                f.write(resp2.content)
                            return {
                                'file_path': file_path,
                                'title': 'Instagram Video',
                                'duration': 0,
                                'filesize': os.path.getsize(file_path),
                                'thumbnail': '',
                                'platform': 'Instagram',
                                'width': 0,
                                'height': 0,
                            }
    except Exception as e:
        logger.error(f"Instagram fallback xatosi: {e}")
    return None


def _download_with_ytdlp(url: str) -> dict:
    """yt-dlp orqali yuklash (YouTube, Facebook, Twitter va boshqalar)"""
    output_template = os.path.join(TEMP_DIR, "%(id)s.%(ext)s")

    ydl_opts = {
        'outtmpl': output_template,
        'format': 'best[filesize<2G]/bestvideo[filesize<2G]+bestaudio/best',
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 3,
        'extractor_retries': 3,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        },
    }

    # Impersonation qo'shish (agar curl_cffi mavjud bo'lsa)
    try:
        import curl_cffi
        ydl_opts['impersonate'] = 'chrome'
    except ImportError:
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
                logger.error(f"Fayl topilmadi: {file_path}")
                return None

            return {
                'file_path': file_path,
                'title': info.get('title', 'Video'),
                'duration': info.get('duration', 0),
                'filesize': os.path.getsize(file_path),
                'thumbnail': info.get('thumbnail', ''),
                'platform': get_platform_from_url(url),
                'width': info.get('width', 0),
                'height': info.get('height', 0),
            }

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"yt-dlp xatosi: {e}")
        return None
    except Exception as e:
        logger.error(f"Video yuklashda xatolik: {e}")
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
