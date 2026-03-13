from .database import Database
from datetime import datetime


class GroupDatabase:
    def __init__(self, db: Database):
        self.db = db

    async def create_table_groups(self):
        sql = """
        CREATE TABLE IF NOT EXISTS Groups (
            id SERIAL PRIMARY KEY,
            group_id BIGINT NOT NULL UNIQUE,
            group_name VARCHAR(255) NOT NULL,
            member_count INTEGER NOT NULL DEFAULT 0,
            joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_activity TIMESTAMP NULL
        );
        """
        await self.db.execute(sql)

    async def add_group(self, group_id: int, group_name: str, member_count: int):
        sql = """
        INSERT INTO Groups(group_id, group_name, member_count, joined_at)
        VALUES ($1, $2, $3, $4)
        """
        await self.db.execute(sql, group_id, group_name, member_count, datetime.now())

    async def update_group_member_count(self, group_id: int, member_count: int):
        sql = "UPDATE Groups SET member_count = $1, last_activity = $2 WHERE group_id = $3"
        await self.db.execute(sql, member_count, datetime.now(), group_id)

    async def get_all_groups(self):
        return await self.db.execute("SELECT * FROM Groups", fetch=True)

    async def delete_group(self, group_id: int):
        await self.db.execute("DELETE FROM Groups WHERE group_id = $1", group_id)
