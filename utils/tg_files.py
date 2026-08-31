"""
Local Bot API server bilan ishlash uchun fayl helperlari.

- sendable(): fayl shared volume da bo'lsa file:// URL qaytaradi (zero-copy,
  bir zumda "upload"), aks holda InputFile (multipart upload).
- download_tg_file(): local Bot API da get_file() absolut yo'l qaytaradi
  (shared volume orqali ko'rinadi) — nusxalash kifoya; cloud API da odatdagi
  HTTP yuklash.
"""
import os
import shutil
import logging
from urllib.parse import quote

from aiogram.types import InputFile

from loader import bot

logger = logging.getLogger(__name__)

LOCAL_BOT_API = bool(os.getenv("BOT_API_URL"))
SHARED_TEMP_DIR = os.getenv("SHARED_TEMP_DIR", "")


def sendable(file_path: str):
    """Yuborish uchun eng tez ko'rinish: file:// URL yoki InputFile"""
    if LOCAL_BOT_API and SHARED_TEMP_DIR and file_path.startswith(SHARED_TEMP_DIR):
        # Bo'sh joy/maxsus belgilar URI ni buzmasligi uchun percent-encode
        return "file://" + quote(file_path)
    return InputFile(file_path)


async def download_tg_file(file_id: str, destination: str) -> bool:
    """Telegram dan fayl yuklash — local Bot API da diskdan nusxalash"""
    try:
        file_info = await bot.get_file(file_id)
        file_path = file_info.file_path

        # Local Bot API: absolut yo'l, volume orqali bizda ham ko'rinadi
        if file_path and os.path.isabs(file_path) and os.path.isfile(file_path):
            shutil.copyfile(file_path, destination)
            return True

        await bot.download_file(file_path, destination=destination)
        return os.path.isfile(destination)
    except Exception as e:
        logger.error(f"[tg_files] yuklash xatosi: {e}")
    return False
