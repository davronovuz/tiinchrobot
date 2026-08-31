import os

from aiogram import Bot, Dispatcher, types
from aiogram.bot.api import TelegramAPIServer
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.contrib.fsm_storage.redis import RedisStorage2
from utils.db_api.database import Database
from utils.db_api.users import UserDatabase
from utils.db_api.groups import GroupDatabase
from utils.db_api.channels import ChannelDatabase
from utils.db_api.cache import MediaCacheDatabase

from data import config

# Local Bot API server (2GB limit, tez upload). BOT_API_URL bo'lmasa — rasmiy cloud API
BOT_API_URL = os.getenv("BOT_API_URL", "")
if BOT_API_URL:
    bot = Bot(
        token=config.BOT_TOKEN,
        parse_mode=types.ParseMode.HTML,
        server=TelegramAPIServer.from_base(BOT_API_URL),
    )
else:
    bot = Bot(token=config.BOT_TOKEN, parse_mode=types.ParseMode.HTML)
# FSM state Redis da — bot restart bo'lsa ham foydalanuvchi holati saqlanadi
try:
    storage = RedisStorage2(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=1,
        pool_size=20,
        prefix="fsm",
        state_ttl=7 * 86400,
        data_ttl=7 * 86400,
        bucket_ttl=7 * 86400,
    )
except Exception:
    storage = MemoryStorage()

dp = Dispatcher(bot, storage=storage)

# Database obyekti (pool on_startup da yaratiladi)
db = Database()

# Database manager obyektlari
user_db = UserDatabase(db=db)
group_db = GroupDatabase(db=db)
channel_db = ChannelDatabase(db=db)
cache_db = MediaCacheDatabase(db=db)

# Redis (on_startup da ulanadi)
redis_client = None
