import os
import asyncio
import tempfile
import logging
import yt_dlp

logger = logging.getLogger(__name__)

# Bir vaqtda maksimum yuklab olish
MAX_CONCURRENT_DOWNLOADS = 5
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# Vaqtinchalik fayllar papkasi
TEMP_DIR = tempfile.mkdtemp(prefix="tiinchbot_")

# Qo'llab-quvvatlanadigan platformalar
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
    yt-dlp orqali video yuklab olish.
    Returns: {
        'file_path': str,
        'title': str,
        'duration': int,
        'filesize': int,
        'thumbnail': str,
        'platform': str,
        'width': int,
        'height': int,
    }
    """
    async with download_semaphore:
        return await asyncio.get_event_loop().run_in_executor(
            None, _download_sync, url
        )


def _download_sync(url: str) -> dict:
    """Sinxron yt-dlp yuklash (executor ichida ishlaydi)"""
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
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return None

            file_path = ydl.prepare_filename(info)

            # Ba'zan ext o'zgarishi mumkin (masalan, merging natijasida)
            if not os.path.exists(file_path):
                base, _ = os.path.splitext(file_path)
                for ext in ['.mp4', '.mkv', '.webm', '.mp3', '.m4a']:
                    candidate = base + ext
                    if os.path.exists(candidate):
                        file_path = candidate
                        break

            if not os.path.exists(file_path):
                logger.error(f"Yuklab olingan fayl topilmadi: {file_path}")
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
        logger.error(f"yt-dlp yuklash xatosi: {e}")
        return None
    except Exception as e:
        logger.error(f"Video yuklashda xatolik: {e}")
        return None


def cleanup_file(file_path: str):
    """Vaqtinchalik faylni o'chirish"""
    try:
        if file_path and os.path.exists(file_path):
            os.unlink(file_path)
            logger.info(f"Vaqtinchalik fayl o'chirildi: {file_path}")
    except Exception as e:
        logger.error(f"Faylni o'chirishda xatolik: {e}")


def cleanup_temp_dir():
    """Barcha vaqtinchalik fayllarni tozalash"""
    try:
        for f in os.listdir(TEMP_DIR):
            filepath = os.path.join(TEMP_DIR, f)
            if os.path.isfile(filepath):
                os.unlink(filepath)
        logger.info("Vaqtinchalik fayllar tozalandi")
    except Exception as e:
        logger.error(f"Temp papkani tozalashda xatolik: {e}")
