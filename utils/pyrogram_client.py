import logging
from pyrogram import Client
from data.config import API_ID, API_HASH, BOT_TOKEN

logger = logging.getLogger(__name__)

pyro_client = None


async def start_pyrogram():
    """Pyrogram client ni ishga tushirish (katta fayllar uchun)"""
    global pyro_client
    if not API_ID or not API_HASH:
        logger.warning("API_ID yoki API_HASH berilmagan, pyrogram ishlamaydi")
        return None

    pyro_client = Client(
        name="tiinchbot_pyro",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        workdir="data",
        no_updates=True,  # Faqat fayl yuborish uchun, updatelarni olmaymiz
    )
    await pyro_client.start()
    logger.info("Pyrogram client ishga tushdi")
    return pyro_client


async def stop_pyrogram():
    """Pyrogram client ni to'xtatish"""
    global pyro_client
    if pyro_client:
        await pyro_client.stop()
        pyro_client = None
        logger.info("Pyrogram client to'xtatildi")


async def send_large_video(chat_id: int, file_path: str, caption: str = "",
                           duration: int = 0, width: int = 0, height: int = 0) -> str:
    """
    Pyrogram orqali katta video yuborish (2GB gacha).
    Returns: file_id yoki None
    """
    if not pyro_client:
        logger.error("Pyrogram client ishlamayapti")
        return None

    try:
        msg = await pyro_client.send_video(
            chat_id=chat_id,
            video=file_path,
            caption=caption,
            duration=duration,
            width=width,
            height=height,
            supports_streaming=True,
        )
        if msg.video:
            return msg.video.file_id
        return None
    except Exception as e:
        logger.error(f"Pyrogram orqali video yuborishda xatolik: {e}")
        return None
