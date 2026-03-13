from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from utils.db_api.database import Database
from utils.db_api.users import UserDatabase
from utils.db_api.groups import GroupDatabase
from utils.db_api.channels import ChannelDatabase
from utils.db_api.cache import MediaCacheDatabase

from data import config

bot = Bot(token=config.BOT_TOKEN, parse_mode=types.ParseMode.HTML)
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
