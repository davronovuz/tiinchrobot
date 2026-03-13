from environs import Env

env = Env()
env.read_env()

BOT_TOKEN = env.str("BOT_TOKEN")
ADMINS = list(map(int, env.list("ADMINS")))
IP = env.str("ip", "localhost")

# PostgreSQL
DB_HOST = env.str("DB_HOST", "localhost")
DB_PORT = env.int("DB_PORT", 5432)
DB_NAME = env.str("DB_NAME", "tiinchrobot")
DB_USER = env.str("DB_USER", "botuser")
DB_PASS = env.str("DB_PASS", "botpassword")

# Redis
REDIS_HOST = env.str("REDIS_HOST", "localhost")
REDIS_PORT = env.int("REDIS_PORT", 6379)

# Pyrogram
API_ID = env.int("API_ID", 0)
API_HASH = env.str("API_HASH", "")

# PostgreSQL DSN
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
