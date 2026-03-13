from aiogram import executor
import redis.asyncio as aioredis
import logging

from loader import dp, db, cache_db, user_db, group_db, channel_db
import middlewares, filters, handlers
from utils.notify_admins import on_startup_notify
from utils.set_bot_commands import set_default_commands
from utils.pyrogram_client import start_pyrogram, stop_pyrogram
from utils.video_downloader import cleanup_temp_dir
from data.config import DATABASE_URL, REDIS_HOST, REDIS_PORT
import loader

logger = logging.getLogger(__name__)


async def on_startup(dispatcher):
    # PostgreSQL pool yaratish
    await db.create_pool(dsn=DATABASE_URL)
    logger.info("PostgreSQL ga ulandi")

    # Redis ga ulanish
    redis_client = aioredis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True
    )
    loader.redis_client = redis_client
    cache_db.redis = redis_client
    logger.info("Redis ga ulandi")

    # Jadvallarni yaratish
    try:
        await user_db.create_table_users()
        await group_db.create_table_groups()
        await channel_db.create_table_channels()
        await cache_db.create_table_cache()
        await cache_db.create_table_request_stats()
        logger.info("Jadvallar yaratildi")
    except Exception as err:
        logger.error(f"Jadval yaratishda xatolik: {err}")

    # Pyrogram ni ishga tushirish (katta fayllar uchun)
    try:
        await start_pyrogram()
    except Exception as err:
        logger.warning(f"Pyrogram ishga tushmadi (katta fayllar yuborilmaydi): {err}")

    # Komandalar
    await set_default_commands(dispatcher)

    # Admin xabarnomasi
    await on_startup_notify(dispatcher)


async def on_shutdown(dispatcher):
    # Pyrogram ni to'xtatish
    await stop_pyrogram()

    # Vaqtinchalik fayllarni tozalash
    cleanup_temp_dir()

    # Redis ni yopish
    if loader.redis_client:
        await loader.redis_client.close()

    # PostgreSQL ni yopish
    await db.close()

    logger.info("Bot to'xtatildi")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown)
