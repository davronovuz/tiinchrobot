from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.filters.state import State, StatesGroup
import logging

from data.config import ADMINS
from loader import dp, user_db
from keyboards.default.default_keyboard import menu_ichki_admin, menu_admin


class AdminStates(StatesGroup):
    AddAdmin = State()
    RemoveAdmin = State()


async def check_super_admin_permission(telegram_id: int):
    return telegram_id in ADMINS


async def check_admin_permission(telegram_id: int):
    user = await user_db.select_user(telegram_id=telegram_id)
    if not user:
        return False
    user_id = user["id"]
    admin = await user_db.check_if_admin(user_id=user_id)
    return admin


@dp.message_handler(Text("🔙 Ortga qaytish"))
async def back_handler(message: types.Message):
    telegram_id = message.from_user.id
    if await check_super_admin_permission(telegram_id) or await check_admin_permission(telegram_id):
        await message.answer("Siz bosh sahifadasiz", reply_markup=menu_admin)


@dp.message_handler(commands="panel")
async def control_panel(message: types.Message):
    telegram_id = message.from_user.id
    if await check_super_admin_permission(telegram_id) or await check_admin_permission(telegram_id):
        await message.answer("Admin panelga xush kelibsiz! 📊", reply_markup=menu_admin)
    else:
        await message.reply("Siz admin emassiz ❌")


@dp.message_handler(Text(equals="👥 Adminlar boshqaruvi"))
async def admin_control_menu(message: types.Message):
    telegram_id = message.from_user.id
    if not await check_super_admin_permission(telegram_id):
        await message.reply("Ushbu amalni faqat super adminlar amalga oshirishi mumkin ❌")
        return
    await message.answer("Admin boshqaruvi menyusiga xush kelibsiz. Kerakli bo'limni tanlang:", reply_markup=menu_ichki_admin)


@dp.message_handler(Text(equals="➕ Admin qo'shish"))
async def add_admin(message: types.Message):
    telegram_id = message.from_user.id
    if not await check_super_admin_permission(telegram_id):
        await message.reply("Ushbu amalni faqat super adminlar amalga oshirishi mumkin ❌")
        return
    await message.answer("Yangi adminning Telegram ID raqamini kiriting .")
    await AdminStates.AddAdmin.set()


@dp.message_handler(state=AdminStates.AddAdmin)
async def process_admin_add(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Iltimos, to'g'ri Telegram ID kiriting (raqam bo'lishi kerak).")
        return

    admin_telegram_id = int(message.text)
    user = await user_db.select_user(telegram_id=admin_telegram_id)

    if not user:
        await message.answer("❌ Bunday foydalanuvchi topilmadi. Avval foydalanuvchini tizimga qo'shing.")
        await state.finish()
        return

    user_id = user["id"]

    if await user_db.check_if_admin(user_id=user_id):
        await message.answer("❌ Bu foydalanuvchi allaqachon admin sifatida ro'yxatga olingan.")
        await state.finish()
        return

    await user_db.add_admin(user_id=user_id, name=user["username"])
    await message.answer(f"✅ @{user['username']} ismli foydalanuvchi admin sifatida qo'shildi!")
    await state.finish()


@dp.message_handler(Text(equals="❌ Adminni o'chirish"))
async def remove_admin(message: types.Message):
    telegram_id = message.from_user.id
    if not await check_super_admin_permission(telegram_id):
        await message.reply("Ushbu amalni faqat super adminlar amalga oshirishi mumkin ❌")
        return
    await message.answer("O'chirilishi kerak bo'lgan adminning Telegram ID raqamini kiriting.")
    await AdminStates.RemoveAdmin.set()


@dp.message_handler(state=AdminStates.RemoveAdmin)
async def process_admin_remove(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Iltimos, to'g'ri Telegram ID kiriting (raqam bo'lishi kerak).")
        return

    admin_telegram_id = int(message.text)
    user = await user_db.select_user(telegram_id=admin_telegram_id)

    if not user:
        await message.answer("❌ Bunday foydalanuvchi topilmadi.")
        await state.finish()
        return

    user_id = user["id"]

    if not await user_db.check_if_admin(user_id=user_id):
        await message.answer("❌ Bu foydalanuvchi admin emas.")
        await state.finish()
        return

    if admin_telegram_id in ADMINS:
        await message.answer("❌ Super adminni o'chirishga ruxsat berilmagan.")
        await state.finish()
        return

    await user_db.remove_admin(user_id=user_id)
    await message.answer(f"✅ @{user['username']} ismli foydalanuvchi adminlikdan o'chirildi!")
    await state.finish()


@dp.message_handler(Text(equals="👥 Barcha adminlar"))
async def list_all_admins(message: types.Message):
    telegram_id = message.from_user.id
    if not await check_super_admin_permission(telegram_id) and not await check_admin_permission(telegram_id):
        await message.reply("Siz admin emassiz ❌")
        return

    admins = await user_db.get_all_admins()
    admin_list = []

    if admins:
        for admin in admins:
            is_super_admin = '✅' if admin['is_super_admin'] else '❌'
            admin_list.append(f"ID: {admin['telegram_id']} | Ism: {admin['name']} | Super Admin: {is_super_admin}")

    for admin_id in ADMINS:
        if not any(admin['telegram_id'] == admin_id for admin in admins):
            admin_list.append(f"ID: {admin_id} | Ism: Super Admin | Super Admin: ✅")

    if admin_list:
        full_admin_list = "\n".join(admin_list)
        await message.answer(f"👥 Adminlar ro'yxati:\n\n{full_admin_list}")
    else:
        await message.answer("❌ Hozircha tizimda hech qanday admin mavjud emas.")
