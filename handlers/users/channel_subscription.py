from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.filters.state import State, StatesGroup
import logging

from data.config import ADMINS
from loader import dp, channel_db, bot, user_db
from keyboards.default.default_keyboard import menu_admin, menu_ichki_kanal


class ChannelStates(StatesGroup):
    AddChannelInviteLink = State()
    AddChannelForwardMessage = State()
    RemoveChannel = State()


async def check_super_admin_permission(telegram_id: int):
    return telegram_id in ADMINS


async def check_admin_permission(telegram_id: int):
    user = await user_db.select_user(telegram_id=telegram_id)
    if not user:
        return False
    user_id = user["id"]
    admin = await user_db.check_if_admin(user_id=user_id)
    return admin


@dp.message_handler(Text(equals="📢 Kanallar boshqaruvi"))
async def channel_management(message: types.Message):
    telegram_id = message.from_user.id
    if await check_super_admin_permission(telegram_id) or await check_admin_permission(telegram_id):
        await message.answer("Kanallar boshqaruvi", reply_markup=menu_ichki_kanal)


@dp.message_handler(Text(equals="➕ Kanal qo'shish"))
async def add_channel(message: types.Message):
    telegram_id = message.from_user.id
    if await check_super_admin_permission(telegram_id) or await check_admin_permission(telegram_id):
        await message.answer("Yangi kanalning taklif (invite) linkini kiriting yoki.")
        await ChannelStates.AddChannelInviteLink.set()


@dp.message_handler(state=ChannelStates.AddChannelInviteLink)
async def process_channel_invite_link(message: types.Message, state: FSMContext):
    invite_link = message.text.strip()
    await state.update_data(invite_link=invite_link)
    await message.answer("Endi kanalning istalgan xabarini oldinga yuboring (forward).")
    await ChannelStates.AddChannelForwardMessage.set()


@dp.message_handler(state=ChannelStates.AddChannelForwardMessage, content_types=types.ContentTypes.ANY)
async def process_channel_forward_message(message: types.Message, state: FSMContext):
    if not message.forward_from_chat:
        await message.answer("Iltimos, kanalning xabarini oldinga yuboring (forward).")
        return

    channel_id = message.forward_from_chat.id
    title = message.forward_from_chat.title
    data = await state.get_data()
    invite_link = data.get('invite_link')

    try:
        bot_member = await bot.get_chat_member(chat_id=channel_id, user_id=(await bot.me).id)
        if bot_member.status not in ['administrator', 'creator']:
            await message.answer("❌ Bot ushbu kanalga administrator sifatida qo'shilmagan.")
            await state.finish()
            return

        await channel_db.add_channel(channel_id=channel_id, title=title, invite_link=invite_link)
        await message.answer(f"✅ {title} kanali muvaffaqiyatli qo'shildi!")
    except Exception as e:
        logging.error(f"Error adding channel: {e}")
        await message.answer("❌ Kanalni qo'shishda xatolik yuz berdi.")

    await state.finish()


@dp.message_handler(Text(equals="❌ Kanalni o'chirish"))
async def remove_channel(message: types.Message):
    telegram_id = message.from_user.id
    if await check_super_admin_permission(telegram_id) or await check_admin_permission(telegram_id):
        await message.answer("O'chirilishi kerak bo'lgan kanalning invite linkini kiriting.")
        await ChannelStates.RemoveChannel.set()


@dp.message_handler(state=ChannelStates.RemoveChannel)
async def process_channel_remove(message: types.Message, state: FSMContext):
    channel_identifier = message.text
    try:
        if channel_identifier.lstrip('-').isdigit():
            channel_id = int(channel_identifier)
            await channel_db.remove_channel(channel_id=channel_id)
            await message.answer(f"✅ Kanal ID {channel_id} muvaffaqiyatli o'chirildi!")
        else:
            channel_data = await channel_db.get_channel_by_invite_link(channel_identifier)
            if channel_data:
                channel_id = channel_data["channel_id"]
                await channel_db.remove_channel(channel_id=channel_id)
                await message.answer(f"✅ Kanal {channel_data['title']} muvaffaqiyatli o'chirildi!")
            else:
                await message.answer("❌ Bunday kanal topilmadi.")
    except Exception as e:
        logging.error(f"Error removing channel: {e}")
        await message.answer("❌ Kanalni o'chirishda xatolik yuz berdi.")

    await state.finish()


@dp.message_handler(Text(equals="📋 Barcha kanallar"))
async def list_all_channels(message: types.Message):
    telegram_id = message.from_user.id
    if await check_super_admin_permission(telegram_id) or await check_admin_permission(telegram_id):
        channels = await channel_db.get_all_channels()
        if not channels:
            await message.answer("❌ Hozircha tizimda hech qanday kanal mavjud emas.")
            return

        channel_list = []
        for channel in channels:
            channel_list.append(f"ID: {channel['channel_id']} | Nomi: {channel['title']} | Invite Link: {channel['invite_link']}")

        full_channel_list = "\n".join(channel_list)
        await message.answer(f"📋 Kanallar ro'yxati:\n\n{full_channel_list}")
