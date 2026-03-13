from .database import Database


class ChannelDatabase:
    def __init__(self, db: Database):
        self.db = db

    async def create_table_channels(self):
        sql = """
        CREATE TABLE IF NOT EXISTS Channels (
            id SERIAL PRIMARY KEY,
            channel_id BIGINT UNIQUE,
            title VARCHAR(255),
            invite_link VARCHAR(255) NOT NULL
        );
        """
        await self.db.execute(sql)

    async def add_channel(self, channel_id: int, title: str, invite_link: str):
        sql = "INSERT INTO Channels (channel_id, title, invite_link) VALUES ($1, $2, $3)"
        await self.db.execute(sql, channel_id, title, invite_link)

    async def remove_channel(self, channel_id: int):
        await self.db.execute("DELETE FROM Channels WHERE channel_id = $1", channel_id)

    async def get_all_channels(self):
        return await self.db.execute("SELECT * FROM Channels", fetch=True)

    async def get_channel_by_id(self, channel_id: int):
        return await self.db.execute(
            "SELECT * FROM Channels WHERE channel_id = $1", channel_id, fetchrow=True
        )

    async def get_channel_by_invite_link(self, invite_link: str):
        return await self.db.execute(
            "SELECT * FROM Channels WHERE invite_link = $1", invite_link, fetchrow=True
        )

    async def update_channel_invite_link(self, channel_id: int, new_invite_link: str):
        sql = "UPDATE Channels SET invite_link = $1 WHERE channel_id = $2"
        await self.db.execute(sql, new_invite_link, channel_id)

    async def channel_exists(self, channel_id: int) -> bool:
        result = await self.db.execute(
            "SELECT 1 FROM Channels WHERE channel_id = $1", channel_id, fetchrow=True
        )
        return result is not None

    async def count_channels(self):
        return await self.db.execute("SELECT COUNT(*) FROM Channels;", fetchval=True)
