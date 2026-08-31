"""
Inline mode — istalgan chatda `@bot qo'shiq nomi` yozib musiqa yuborish.

Cache'dagi (avval yuklangan) musiqalar bir zumda audio sifatida chiqadi.
Cache'da bo'lmasa — botga o'tib yuklash taklifi ko'rsatiladi.
"""
import logging

from aiogram import types
from aiogram.types import (
    InlineQueryResultCachedAudio,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from loader import dp, cache_db
from handlers.users.music_search import search_music

logger = logging.getLogger(__name__)

BOT_USERNAME = "tinchrobot"


def _pretty(url_key: str) -> str:
    """'music:artist - title' -> 'Artist - Title'"""
    name = url_key[len("music:"):] if url_key.startswith("music:") else url_key
    return name.strip().title()[:100] or "Audio"


@dp.inline_handler()
async def inline_music_handler(query: types.InlineQuery):
    text = (query.query or "").strip()

    if len(text) < 2:
        await query.answer(
            results=[],
            cache_time=10,
            is_personal=True,
            switch_pm_text="🎵 Qo'shiq nomini yozing",
            switch_pm_parameter="inline_help",
        )
        return

    results = []

    # 1. Cache'dagi musiqalar — bir zumda yuboriladi
    try:
        rows = await cache_db.search_music_cache(text, limit=20)
        for row in rows or []:
            results.append(
                InlineQueryResultCachedAudio(
                    id=f"c{abs(hash(row['url'])) % 10**12}",
                    audio_file_id=row["file_id"],
                    caption="✨ @tinchrobot – Tinchlikni xohlovchilar uchun!",
                )
            )
    except Exception as e:
        logger.error(f"[inline] cache qidiruv xatosi: {e}")

    # 2. Cache bo'sh bo'lsa — qidiruv natijalarini ko'rsatib, botga yo'naltirish
    if not results:
        try:
            found = await search_music(text)
        except Exception as e:
            logger.error(f"[inline] qidiruv xatosi: {e}")
            found = []

        for i, item in enumerate(found[:15]):
            name = f"{item.get('artist', '')} - {item.get('title', '')}".strip(" -")
            results.append(
                InlineQueryResultArticle(
                    id=f"s{i}",
                    title=name[:100] or "Audio",
                    description="Yuklab olish uchun bosing",
                    input_message_content=InputTextMessageContent(
                        message_text=name,
                    ),
                    thumb_url=item.get("album_cover") or None,
                )
            )

    await query.answer(
        results=results,
        cache_time=60,
        is_personal=True,
        switch_pm_text="🎵 Botda yuklash",
        switch_pm_parameter="start",
    )
