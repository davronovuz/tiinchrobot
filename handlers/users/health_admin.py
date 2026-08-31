"""
/health — admin uchun platforma holati hisoboti.
"""
import logging

from aiogram import types

from data.config import ADMINS
from loader import dp, bot
from utils import health

logger = logging.getLogger(__name__)


async def _is_admin(telegram_id: int) -> bool:
    if telegram_id in ADMINS:
        return True
    from handlers.users.admin_panel import check_admin_permission
    try:
        return bool(await check_admin_permission(telegram_id))
    except Exception:
        return False


@dp.message_handler(commands=["health", "holat"])
async def health_handler(message: types.Message):
    if not await _is_admin(message.from_user.id):
        return

    text = await health.get_report()
    text += await health.get_music_report()

    # Xizmatlar holati
    text += "\n\n🔧 <b>Xizmatlar:</b>"
    text += f"\n{'🟢' if await _ping_service('cobalt') else '🔴'} Cobalt"
    text += f"\n{'🟢' if await _ping_service('bgutil') else '🔴'} bgutil PO-token"
    text += f"\n{'🟢' if await _ping_warp() else '🔴'} WARP proxy"

    await message.answer(text, parse_mode="HTML")


async def _ping_service(name: str) -> bool:
    import os
    from utils.video_downloader import get_http_client
    urls = {
        "cobalt": os.getenv("COBALT_API_URL", "http://cobalt:9000"),
        "bgutil": os.getenv("BGUTIL_URL", "http://bgutil:4416") + "/ping",
    }
    try:
        resp = await get_http_client().get(urls[name], timeout=5)
        return resp.status_code < 500
    except Exception:
        return False


async def _ping_warp() -> bool:
    import httpx
    from utils.video_downloader import WARP_PROXY
    try:
        async with httpx.AsyncClient(proxy=WARP_PROXY, timeout=8, verify=False) as client:
            resp = await client.get("https://www.cloudflare.com/cdn-cgi/trace")
            return resp.status_code == 200
    except Exception:
        return False


async def notify_admins_alert(text: str):
    """health.record_failure qaytargan alertni adminlarga yuborish"""
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logger.debug(f"[health alert] {admin_id} ga yuborilmadi: {e}")
