from .database import Database
from datetime import datetime, timedelta


class UserDatabase:
    def __init__(self, db: Database):
        self.db = db

    async def create_table_users(self):
        sql_users = """
        CREATE TABLE IF NOT EXISTS Users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL UNIQUE,
            username VARCHAR(255) NULL,
            last_active TIMESTAMP NULL,
            is_active BOOLEAN DEFAULT TRUE,
            is_blocked BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
        await self.db.execute(sql_users)

        sql_admins = """
        CREATE TABLE IF NOT EXISTS Admins (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            is_super_admin BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
        await self.db.execute(sql_admins)

    # Foydalanuvchilar bilan ishlash
    async def user_exists(self, telegram_id: int):
        sql = "SELECT 1 FROM Users WHERE telegram_id = $1"
        result = await self.db.execute(sql, telegram_id, fetchrow=True)
        return result is not None

    async def add_user(self, telegram_id: int, username: str, created_at=None):
        if not await self.user_exists(telegram_id):
            if created_at is None:
                created_at = datetime.now()
            sql = """
            INSERT INTO Users (telegram_id, username, created_at)
            VALUES ($1, $2, $3)
            """
            await self.db.execute(sql, telegram_id, username, created_at)

    async def select_all_users(self):
        sql = "SELECT * FROM Users"
        return await self.db.execute(sql, fetch=True)

    async def select_user(self, **kwargs):
        conditions = []
        values = []
        for i, (key, val) in enumerate(kwargs.items(), 1):
            conditions.append(f"{key} = ${i}")
            values.append(val)
        sql = f"SELECT * FROM Users WHERE {' AND '.join(conditions)}"
        return await self.db.execute(sql, *values, fetchrow=True)

    async def count_users(self):
        return await self.db.execute("SELECT COUNT(*) FROM Users;", fetchval=True)

    async def delete_users(self):
        await self.db.execute("DELETE FROM Users")

    async def update_user_last_active(self, telegram_id: int):
        sql = "UPDATE Users SET last_active = $1 WHERE telegram_id = $2"
        await self.db.execute(sql, datetime.now(), telegram_id)

    async def deactivate_user(self, telegram_id: int):
        sql = "UPDATE Users SET is_active = FALSE WHERE telegram_id = $1"
        await self.db.execute(sql, telegram_id)

    async def activate_user(self, telegram_id: int):
        sql = "UPDATE Users SET is_active = TRUE WHERE telegram_id = $1"
        await self.db.execute(sql, telegram_id)

    async def mark_user_as_blocked(self, telegram_id: int):
        sql = "UPDATE Users SET is_blocked = TRUE, is_active = FALSE WHERE telegram_id = $1"
        await self.db.execute(sql, telegram_id)

    async def get_active_users(self):
        sql = "SELECT * FROM Users WHERE is_active = TRUE"
        return await self.db.execute(sql, fetch=True)

    async def get_inactive_users(self):
        sql = "SELECT * FROM Users WHERE is_active = FALSE"
        return await self.db.execute(sql, fetch=True)

    async def get_blocked_users(self):
        sql = "SELECT * FROM Users WHERE is_blocked = TRUE"
        return await self.db.execute(sql, fetch=True)

    # Statistikalar
    async def count_active_users(self):
        return await self.db.execute(
            "SELECT COUNT(*) FROM Users WHERE is_active = TRUE;", fetchval=True
        )

    async def count_blocked_users(self):
        return await self.db.execute(
            "SELECT COUNT(*) FROM Users WHERE is_blocked = TRUE;", fetchval=True
        )

    async def count_users_last_12_hours(self):
        time_threshold = datetime.now() - timedelta(hours=12)
        sql = "SELECT COUNT(*) FROM Users WHERE created_at >= $1;"
        return await self.db.execute(sql, time_threshold, fetchval=True)

    async def count_users_today(self):
        today = datetime.now().date()
        sql = "SELECT COUNT(*) FROM Users WHERE DATE(created_at) = $1;"
        return await self.db.execute(sql, today, fetchval=True)

    async def count_users_this_week(self):
        start_of_week = (datetime.now() - timedelta(days=datetime.now().weekday())).date()
        sql = "SELECT COUNT(*) FROM Users WHERE DATE(created_at) >= $1;"
        return await self.db.execute(sql, start_of_week, fetchval=True)

    async def count_users_this_month(self):
        start_of_month = datetime.now().replace(day=1).date()
        sql = "SELECT COUNT(*) FROM Users WHERE DATE(created_at) >= $1;"
        return await self.db.execute(sql, start_of_month, fetchval=True)

    # Adminlar bilan ishlash
    async def add_admin(self, user_id: int, name: str, is_super_admin: bool = False):
        if not await self.check_if_admin(user_id):
            sql = "INSERT INTO Admins (user_id, name, is_super_admin) VALUES ($1, $2, $3)"
            await self.db.execute(sql, user_id, name, is_super_admin)

    async def remove_admin(self, user_id: int):
        sql = "DELETE FROM Admins WHERE user_id = $1"
        await self.db.execute(sql, user_id)

    async def get_all_admins(self):
        sql = """
        SELECT Admins.user_id, Users.telegram_id, Admins.name, Admins.is_super_admin
        FROM Admins
        JOIN Users ON Admins.user_id = Users.id
        """
        rows = await self.db.execute(sql, fetch=True)
        admins = []
        for row in rows:
            admins.append({
                "user_id": row["user_id"],
                "telegram_id": row["telegram_id"],
                "name": row["name"],
                "is_super_admin": row["is_super_admin"]
            })
        return admins

    async def check_if_admin(self, user_id: int) -> bool:
        sql = "SELECT 1 FROM Admins WHERE user_id = $1"
        result = await self.db.execute(sql, user_id, fetchrow=True)
        return result is not None

    async def update_admin_status(self, user_id: int, is_super_admin: bool):
        sql = "UPDATE Admins SET is_super_admin = $1 WHERE user_id = $2"
        await self.db.execute(sql, is_super_admin, user_id)
