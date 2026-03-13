from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from loader import user_db, dp
from data.config import ADMINS


async def check_super_admin_permission(telegram_id: int):
    return telegram_id in ADMINS


async def check_admin_permission(telegram_id: int):
    user = await user_db.select_user(telegram_id=telegram_id)
    if not user:
        return False
    user_id = user["id"]
    admin = await user_db.check_if_admin(user_id=user_id)
    return admin


@dp.message_handler(text="📊 Statistika")
async def admin_statistics_handler(message: types.Message):
    telegram_id = message.from_user.id
    if await check_super_admin_permission(telegram_id) or await check_admin_permission(telegram_id):
        total_users = await user_db.count_users()
        active_users = await user_db.count_active_users()
        inactive_users = total_users - active_users
        users_last_12_hours = await user_db.count_users_last_12_hours()
        users_today = await user_db.count_users_today()
        users_this_week = await user_db.count_users_this_week()
        users_this_month = await user_db.count_users_this_month()
        total_admins = len(await user_db.get_all_admins())

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📊 Batafsil ma'lumot", callback_data="detailed_statistics"))

        stats_text = (
            "\U0001F4CA <b>Bot Statistikalari:</b>\n"
            f"\n👥 <b>Jami foydalanuvchilar:</b> {total_users}"
            f"\n🟢 <b>Faol foydalanuvchilar:</b> {active_users}"
            f"\n🔴 <b>Botni tark etgan foydalanuvchilar:</b> {inactive_users}"
            f"\n🕒 <b>Oxirgi 12 soatda qo'shilgan foydalanuvchilar:</b> {users_last_12_hours}"
            f"\n📅 <b>Bugungi yangi foydalanuvchilar:</b> {users_today}"
            f"\n📈 <b>Haftalik yangi foydalanuvchilar:</b> {users_this_week}"
            f"\n📊 <b>Oylik yangi foydalanuvchilar:</b> {users_this_month}"
            f"\n👮‍♂️ <b>Jami adminlar:</b> {total_admins}"
        )

        await message.answer(stats_text, reply_markup=markup, parse_mode="HTML")


@dp.callback_query_handler(lambda c: c.data == "detailed_statistics")
async def detailed_statistics_callback_handler(call: types.CallbackQuery):
    total_admins = await user_db.get_all_admins()

    admin_details = "\U0001F6E0 <b>Adminlar ro'yxati:</b>\n"
    if not total_admins and not ADMINS:
        admin_details += "\n❌ Hozircha hech qanday admin mavjud emas."
    else:
        for admin in total_admins:
            admin_details += (f"\n🆔 <b>ID:</b> {admin['user_id']}"
                              f"\n👤 <b>Telegram ID:</b> {admin['telegram_id']}"
                              f"\n📛 <b>Ismi:</b> {admin['name']}"
                              f"\n🔑 <b>Super admin:</b> {'✅ Ha' if admin['is_super_admin'] else '❌ Yoq'}\n")

        for admin_id in ADMINS:
            if not any(admin['telegram_id'] == admin_id for admin in total_admins):
                admin_details += f"\n🆔 <b>ID:</b> {admin_id} | 👤 <b>Ism:</b> Super Admin | 🔑 <b>Super Admin:</b> ✅ Ha\n"

    await call.message.edit_text(admin_details, parse_mode="HTML")
    await call.answer()
