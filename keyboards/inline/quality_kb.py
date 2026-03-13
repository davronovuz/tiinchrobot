from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def youtube_quality_keyboard(url_hash: str, formats: list) -> InlineKeyboardMarkup:
    """
    YouTube sifat tanlash klaviaturasi.
    formats: [{"quality": "1080p", "format_id": "137+140", "filesize": "120MB", "ext": "mp4"}, ...]
    """
    kb = InlineKeyboardMarkup(row_width=2)

    for fmt in formats:
        quality = fmt["quality"]
        size_info = fmt.get("size_text", "")
        label = f"🎬 {quality}"
        if size_info:
            label += f" ({size_info})"

        cb_data = f"ytq:{url_hash}:{fmt['format_id']}"
        # Callback data 64 bayt limitiga moslashtirish
        if len(cb_data) > 64:
            cb_data = cb_data[:64]

        kb.insert(InlineKeyboardButton(text=label, callback_data=cb_data))

    # Audio only tugmasi
    kb.add(InlineKeyboardButton(
        text="🎵 Faqat audio (MP3)",
        callback_data=f"ytq:{url_hash}:audio"
    ))

    return kb