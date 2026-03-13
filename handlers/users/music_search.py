import asyncio
import httpx
from bs4 import BeautifulSoup
from aiogram import types
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from io import BytesIO
import logging
from loader import dp, bot, cache_db

from keyboards.default.menu_i import world_track, top_track, main_btn
from utils.misc.download_file import world_music, main_data, top_music, new_trek

logger = logging.getLogger(__name__)


# /tiktok, /top, /new komandalari
@dp.message_handler(commands='tiktok')
async def tik_tok_handler(msg: types.Message):
    text = 'Siz uchun top 10 Tik-Tok Musiqalar!\n\n'
    sana = 1
    for i in world_music():
        text += f"{str(sana)}. {i['artist']} - {i['title']}\n"
        sana += 1
    await msg.answer(text=text, reply_markup=world_track())


@dp.callback_query_handler(lambda x: x.data in [i['id'] for i in world_music()])
async def tik_tok_callback(callback: types.CallbackQuery):
    user_id = callback.data
    for i in world_music():
        if i['id'] == user_id:
            await callback.message.answer_audio(i['track'], f"{i['artist']} - {i['title']}")


@dp.message_handler(commands='top')
async def top_handler(msg: types.Message):
    text = 'Siz uchun top 10 Musiqalar!\n\n'
    sana = 1
    for i in top_music():
        text += f"{str(sana)}. {i['artist']} - {i['title']}\n"
        sana += 1
    await msg.answer(text=text, reply_markup=top_track())


@dp.callback_query_handler(lambda msg: msg.data in [i['id'] for i in top_music()])
async def welcome(callback: types.CallbackQuery):
    region_id = callback.data
    for i in top_music():
        if i['id'] == region_id:
            await callback.message.answer_audio(i['track'], f"{i['artist']} - {i['title']}")


@dp.message_handler(commands='new')
async def new_music_handler(msg: types.Message):
    text = 'Siz uchun 10 yangi Musiqalar!\n\n'
    sana = 1
    for i in new_trek():
        text += f"{str(sana)}. {i['artist']} - {i['title']}\n"
        sana += 1
    await msg.answer(text=text, reply_markup=main_btn())


@dp.callback_query_handler(lambda x: x.data in [i['id'] for i in new_trek()])
async def new_callback_handler(callback: types.CallbackQuery):
    data_id = callback.data
    for i in new_trek():
        if data_id == i['id']:
            await callback.message.answer_audio(i['track'], f"{i['artist']} - {i['title']}")


@dp.callback_query_handler(lambda msg: msg.data == 'remove')
async def remove(callback: types.CallbackQuery):
    await callback.message.delete()


# Foydalanuvchi qidiruv natijalarini saqlash uchun lug'at
user_results = {}


# Qidiruv funksiyalari (3 ta saytdan parallel qidirish)
async def search_music_muztv(query):
    search_url = f"http://muztv.uz/index.php?do=search&subaction=search&story={query}"
    results = []
    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        try:
            response = await client.get(
                search_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15.0
            )
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                for item in soup.find_all("div", class_="play-item"):
                    title = item.get("data-title")
                    artist = item.get("data-artist")
                    url = item.get("data-track")
                    if title and artist and url:
                        if url.startswith("/"):
                            url = f"https://muztv.uz{url}"
                        results.append({"title": title, "artist": artist, "url": url, "source": "muztv"})
        except Exception as e:
            logger.error(f"muztv.uz xatolik: {e}")
    return results


async def search_music_xitmuzon(query):
    search_url = f"https://xitmuzon.net/index.php?do=search&subaction=search&story={query}"
    results = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.get(
                search_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15.0
            )
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                for item in soup.find_all("div", class_="track-item"):
                    title = item.get("data-title")
                    artist = item.get("data-artist")
                    dl_link = item.find("a", class_="track-dl")
                    if title and artist and dl_link:
                        url = dl_link.get("href", "")
                        if url.startswith("/"):
                            url = f"https://xitmuzon.net{url}"
                        if url:
                            results.append({"title": title, "artist": artist, "url": url, "source": "xitmuzon"})
        except Exception as e:
            logger.error(f"xitmuzon.net xatolik: {e}")
    return results


async def search_music_uzhits(query):
    search_url = f"https://uzhits.net/index.php?do=search&subaction=search&story={query}"
    results = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.get(
                search_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15.0
            )
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                for item in soup.find_all("div", class_="track-item"):
                    title = item.get("data-title")
                    artist = item.get("data-artist")
                    url = item.get("data-track")
                    if title and artist and url:
                        if url.startswith("/"):
                            url = f"https://uzhits.net{url}"
                        results.append({"title": title, "artist": artist, "url": url, "source": "uzhits"})
        except Exception as e:
            logger.error(f"uzhits.net xatolik: {e}")
    return results


async def search_music(query):
    """3 ta saytdan parallel qidirish (tezroq)"""
    tasks = [
        search_music_muztv(query),
        search_music_xitmuzon(query),
        search_music_uzhits(query),
    ]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    all_results = []
    seen_titles = set()
    for results in results_list:
        if isinstance(results, Exception):
            logger.error(f"Qidiruv xatosi: {results}")
            continue
        for item in results:
            key = f"{item['artist'].lower()}-{item['title'].lower()}"
            if key not in seen_titles:
                seen_titles.add(key)
                all_results.append(item)

    return all_results


# Xabarni qayta ishlash (musiqa qidirish)
@dp.message_handler()
async def handle_message(message: types.Message):
    search_query = message.text.strip()
    if not search_query:
        await message.reply("Iltimos, qidiruv so'zini kiriting.")
        return

    await bot.send_chat_action(message.chat.id, "typing")

    all_results = await search_music(search_query)

    if all_results:
        user_results[message.chat.id] = {
            "results": all_results,
            "current_page": 1,
            "query": search_query,
        }
        await send_results_page(message.chat.id)
    else:
        await message.reply("Hech qanday natija topilmadi.")


async def send_results_page(chat_id):
    data = user_results.get(chat_id)
    if not data:
        return

    results = data["results"]
    page = data["current_page"]
    items_per_page = 10
    total_pages = (len(results) - 1) // items_per_page + 1
    search_query = data.get("query", "Natijalar")

    start_index = (page - 1) * items_per_page
    end_index = start_index + items_per_page
    page_results = results[start_index:end_index]

    markup = InlineKeyboardMarkup(row_width=5)
    buttons = []
    for idx, info in enumerate(page_results, start=1):
        result_id = start_index + idx - 1
        buttons.append(
            InlineKeyboardButton(text=str(idx), callback_data=f"download:{result_id}:{chat_id}")
        )
    markup.add(*buttons)

    pagination_buttons = []
    if page > 1:
        pagination_buttons.append(
            InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"page:{page - 1}:{chat_id}")
        )
    if page < total_pages:
        pagination_buttons.append(
            InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"page:{page + 1}:{chat_id}")
        )

    clear_button = InlineKeyboardButton(text="❌", callback_data=f"clear:{chat_id}")
    if pagination_buttons:
        pagination_buttons.append(clear_button)
        markup.add(*pagination_buttons)
    else:
        markup.add(clear_button)

    response_text = f"🔍 **{search_query} (sahifa {page}/{total_pages}):**\n\n" + "\n".join(
        [f"{idx}. {info['artist']} - {info['title']}" for idx, info in enumerate(page_results, start=1)]
    )

    old_message_id = data.get("message_id")
    if old_message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_message_id)
        except Exception:
            pass

    sent_message = await bot.send_message(chat_id, response_text, reply_markup=markup, parse_mode="Markdown")
    user_results[chat_id]["message_id"] = sent_message.message_id


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("page:"))
async def pagination_callback_handler(callback_query: CallbackQuery):
    data_parts = callback_query.data.split(":")
    if len(data_parts) == 3:
        _, page_str, chat_id_str = data_parts
        page = int(page_str)
        chat_id = int(chat_id_str)
        user_data = user_results.get(chat_id)
        if user_data:
            user_data["current_page"] = page
            await send_results_page(chat_id)
            await callback_query.answer()
        else:
            await callback_query.answer("Ma'lumot topilmadi.")
    else:
        await callback_query.answer("Noto'g'ri ma'lumot.")


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("clear:"))
async def clear_callback_handler(callback_query: CallbackQuery):
    data_parts = callback_query.data.split(":")
    if len(data_parts) == 2:
        _, chat_id_str = data_parts
        chat_id = int(chat_id_str)
        user_data = user_results.get(chat_id)
        if user_data:
            message_id = user_data.get("message_id")
            if message_id:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                except Exception:
                    pass
            user_results.pop(chat_id, None)
            await callback_query.answer("Natijalar o'chirildi.")
        else:
            await callback_query.answer("Ma'lumot topilmadi.")
    else:
        await callback_query.answer("Noto'g'ri ma'lumot.")


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("download:"))
async def download_callback_handler(callback_query: CallbackQuery):
    data_parts = callback_query.data.split(":")
    if len(data_parts) == 3:
        _, result_id_str, chat_id_str = data_parts
        result_id = int(result_id_str)
        chat_id = int(chat_id_str)

        user_data = user_results.get(chat_id)
        if user_data and 0 <= result_id < len(user_data["results"]):
            music_info = user_data["results"][result_id]
            url = music_info["url"]

            # Avval cache tekshirish
            cached = await cache_db.get_file_id_by_url(url)
            if cached:
                caption = "✨ @tinchrobot – Tinchlikni xohlovchilar uchun!"
                await bot.send_audio(
                    chat_id=callback_query.message.chat.id,
                    audio=cached["file_id"],
                    caption=caption,
                )
                await callback_query.answer()
                return

            await callback_query.answer("Yuklab olinmoqda, biroz kuting...")
            async with httpx.AsyncClient(follow_redirects=True, verify=False) as client:
                try:
                    response = await client.get(
                        url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60.0
                    )
                    if response.status_code == 200:
                        file_data = BytesIO(response.content)
                        file_data.seek(0)
                        file_data.name = f"{music_info['artist']} - {music_info['title']}.mp3"

                        caption = "✨ @tinchrobot – Tinchlikni xohlovchilar uchun!"

                        sent_msg = await bot.send_audio(
                            chat_id=callback_query.message.chat.id,
                            audio=file_data,
                            caption=caption,
                            title=music_info["title"],
                            performer=music_info["artist"],
                        )
                        # Cache ga saqlash
                        if sent_msg.audio:
                            await cache_db.add_cache(
                                music_info["source"], url, sent_msg.audio.file_id, "audio"
                            )
                    else:
                        await callback_query.message.answer("Qo'shiqni yuklab olishda xatolik yuz berdi.")
                except Exception as e:
                    logger.error(f"Qo'shiq yuklash xatosi: {e}")
                    await callback_query.message.answer("Qo'shiqni yuklab olishda xatolik yuz berdi.")
        else:
            await callback_query.answer("Yuklab olish havolasi topilmadi.")
    else:
        await callback_query.answer("Noto'g'ri ma'lumot.")
