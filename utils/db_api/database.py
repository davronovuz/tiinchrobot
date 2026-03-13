import asyncpg
import logging

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self.pool = None

    async def create_pool(self, dsn: str):
        self.pool = await asyncpg.create_pool(dsn=dsn, min_size=5, max_size=20)
        logger.info("PostgreSQL pool yaratildi")

    async def execute(self, sql: str, *args, fetch=False, fetchval=False, fetchrow=False):
        async with self.pool.acquire() as conn:
            if fetch:
                return await conn.fetch(sql, *args)
            elif fetchval:
                return await conn.fetchval(sql, *args)
            elif fetchrow:
                return await conn.fetchrow(sql, *args)
            else:
                return await conn.execute(sql, *args)

    async def close(self):
        if self.pool:
            await self.pool.close()
            logger.info("PostgreSQL pool yopildi")
