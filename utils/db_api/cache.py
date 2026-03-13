from .database import Database
import redis.asyncio as aioredis
import logging

logger = logging.getLogger(__name__)


class MediaCacheDatabase:
    def __init__(self, db: Database, redis_client: aioredis.Redis = None):
        self.db = db
        self.redis = redis_client

    async def create_table_cache(self):
        sql_cache = """
        CREATE TABLE IF NOT EXISTS MediaCache (
            id SERIAL PRIMARY KEY,
            platform TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            file_id TEXT NOT NULL,
            media_type TEXT DEFAULT 'document',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        await self.db.execute(sql_cache)

    async def create_table_request_stats(self):
        sql_stats = """
        CREATE TABLE IF NOT EXISTS RequestStats (
            id SERIAL PRIMARY KEY,
            platform TEXT NOT NULL,
            request_count INTEGER DEFAULT 0,
            created_at DATE DEFAULT CURRENT_DATE
        );
        """
        await self.db.execute(sql_stats)

    # Media cache funksiyalari
    async def add_cache(self, platform: str, url: str, file_id: str, media_type: str = "document"):
        sql = """
        INSERT INTO MediaCache (platform, url, file_id, media_type)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (url) DO NOTHING
        """
        await self.db.execute(sql, platform, url, file_id, media_type)
        # Redis ga ham yozamiz
        if self.redis:
            await self.redis.hset(f"cache:{url}", mapping={
                "file_id": file_id,
                "media_type": media_type
            })
            await self.redis.expire(f"cache:{url}", 86400 * 30)  # 30 kun

    async def get_file_id_by_url(self, url: str):
        # Avval Redis dan qidiramiz (tez)
        if self.redis:
            cached = await self.redis.hgetall(f"cache:{url}")
            if cached:
                return {
                    "file_id": cached.get("file_id", ""),
                    "media_type": cached.get("media_type", "document")
                }

        # Redis da bo'lmasa PostgreSQL dan
        sql = "SELECT file_id, media_type FROM MediaCache WHERE url = $1"
        result = await self.db.execute(sql, url, fetchrow=True)
        if result:
            data = {"file_id": result["file_id"], "media_type": result["media_type"]}
            # Redis ga ham yozamiz
            if self.redis:
                await self.redis.hset(f"cache:{url}", mapping=data)
                await self.redis.expire(f"cache:{url}", 86400 * 30)
            return data
        return None

    async def get_all_cache(self):
        return await self.db.execute("SELECT * FROM MediaCache", fetch=True)

    async def delete_cache_by_url(self, url: str):
        await self.db.execute("DELETE FROM MediaCache WHERE url = $1", url)
        if self.redis:
            await self.redis.delete(f"cache:{url}")

    async def clear_all_cache(self):
        await self.db.execute("DELETE FROM MediaCache")
        if self.redis:
            async for key in self.redis.scan_iter("cache:*"):
                await self.redis.delete(key)

    async def cache_exists(self, url: str) -> bool:
        if self.redis:
            exists = await self.redis.exists(f"cache:{url}")
            if exists:
                return True
        result = await self.db.execute(
            "SELECT 1 FROM MediaCache WHERE url = $1", url, fetchrow=True
        )
        return result is not None

    # Statistikalar
    async def increment_request_count(self, platform: str):
        sql_check = "SELECT id FROM RequestStats WHERE platform = $1 AND created_at = CURRENT_DATE"
        existing = await self.db.execute(sql_check, platform, fetchrow=True)

        if existing:
            sql_update = """
            UPDATE RequestStats SET request_count = request_count + 1
            WHERE platform = $1 AND created_at = CURRENT_DATE
            """
            await self.db.execute(sql_update, platform)
        else:
            sql_insert = "INSERT INTO RequestStats (platform, request_count) VALUES ($1, 1)"
            await self.db.execute(sql_insert, platform)

    async def get_daily_stats(self):
        sql = "SELECT platform, request_count FROM RequestStats WHERE created_at = CURRENT_DATE"
        return await self.db.execute(sql, fetch=True)

    async def get_weekly_stats(self):
        sql = """
        SELECT platform, SUM(request_count) as total_requests
        FROM RequestStats
        WHERE created_at >= CURRENT_DATE - INTERVAL '6 days'
        GROUP BY platform
        """
        return await self.db.execute(sql, fetch=True)

    async def get_monthly_stats(self):
        sql = """
        SELECT platform, SUM(request_count) as total_requests
        FROM RequestStats
        WHERE TO_CHAR(created_at, 'YYYY-MM') = TO_CHAR(CURRENT_DATE, 'YYYY-MM')
        GROUP BY platform
        """
        return await self.db.execute(sql, fetch=True)
