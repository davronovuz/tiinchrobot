"""
Platforma sog'lig'ini kuzatish — muvaffaqiyat/xato hisoblagichlari, avto-alert.

Redis da kunlik hisoblagichlar saqlanadi:
  health:ok:<platform>:<YYYY-MM-DD>    — muvaffaqiyatli yuklashlar
  health:fail:<platform>:<YYYY-MM-DD>  — xatolar
  health:streak:<platform>             — ketma-ket xatolar (muvaffaqiyatda nolga tushadi)
  health:alerted:<platform>            — alert yuborilgan (1 soat TTL, spam bo'lmasin)
"""
import logging
import time
from datetime import date

import loader

logger = logging.getLogger(__name__)

# Ketma-ket shuncha xatodan keyin adminga xabar
ALERT_STREAK = 8
# Bir xil platforma bo'yicha alert oralig'i (sekund)
ALERT_COOLDOWN = 3600
# Hisoblagichlar saqlanish muddati (30 kun)
_TTL = 86400 * 30


def _today() -> str:
    return date.today().isoformat()


async def record_success(platform: str, elapsed: float = 0.0):
    r = loader.redis_client
    if not r:
        return
    try:
        day = _today()
        pipe = r.pipeline()
        pipe.incr(f"health:ok:{platform}:{day}")
        pipe.expire(f"health:ok:{platform}:{day}", _TTL)
        pipe.delete(f"health:streak:{platform}")
        if elapsed:
            pipe.incrbyfloat(f"health:time:{platform}:{day}", elapsed)
            pipe.expire(f"health:time:{platform}:{day}", _TTL)
        await pipe.execute()
    except Exception as e:
        logger.debug(f"[health] success yozishda xato: {e}")


async def record_failure(platform: str, reason: str = ""):
    """Xatoni yozadi. Ketma-ket xatolar chegaradan oshsa alert matnini qaytaradi."""
    r = loader.redis_client
    if not r:
        return None
    try:
        day = _today()
        pipe = r.pipeline()
        pipe.incr(f"health:fail:{platform}:{day}")
        pipe.expire(f"health:fail:{platform}:{day}", _TTL)
        pipe.incr(f"health:streak:{platform}")
        results = await pipe.execute()
        streak = int(results[-1])

        if streak < ALERT_STREAK:
            return None

        # Cooldown — bir soatda bir marta
        alerted = await r.set(
            f"health:alerted:{platform}", str(int(time.time())),
            ex=ALERT_COOLDOWN, nx=True,
        )
        if not alerted:
            return None

        text = (
            f"🚨 <b>{platform}</b> ishlamayapti\n"
            f"Ketma-ket <b>{streak}</b> ta xato.\n"
        )
        if reason:
            text += f"\nOxirgi sabab: <code>{reason[:200]}</code>"
        text += _hint(platform)
        return text
    except Exception as e:
        logger.debug(f"[health] failure yozishda xato: {e}")
    return None


def _hint(platform: str) -> str:
    hints = {
        "Instagram": "\n\n💡 Ehtimol <code>cookies.txt</code> dagi sessionid eskirgan — yangilang.",
        "YouTube": "\n\n💡 WARP proxy yoki bgutil PO-token provayderini tekshiring:\n"
                   "<code>docker compose restart warp bgutil</code>",
        "TikTok": "\n\n💡 tikwm API rate-limit bo'lishi mumkin.",
    }
    return hints.get(platform, "")


async def get_report() -> str:
    """/health uchun hisobot matni"""
    r = loader.redis_client
    if not r:
        return "⚠️ Redis ulanmagan — statistika yo'q."

    day = _today()
    rows = []
    try:
        async for key in r.scan_iter(f"health:ok:*:{day}", count=500):
            platform = key.split(":")[2]
            rows.append(platform)
        async for key in r.scan_iter(f"health:fail:*:{day}", count=500):
            platform = key.split(":")[2]
            rows.append(platform)
    except Exception as e:
        return f"⚠️ Statistikani o'qib bo'lmadi: {e}"

    platforms = sorted(set(rows))
    if not platforms:
        return "📊 Bugun hali so'rov bo'lmagan."

    lines = ["📊 <b>Bugungi platforma holati</b>\n"]
    total_ok = total_fail = 0

    for p in platforms:
        try:
            ok = int(await r.get(f"health:ok:{p}:{day}") or 0)
            fail = int(await r.get(f"health:fail:{p}:{day}") or 0)
            spent = float(await r.get(f"health:time:{p}:{day}") or 0)
            streak = int(await r.get(f"health:streak:{p}") or 0)
        except Exception:
            continue

        total_ok += ok
        total_fail += fail
        total = ok + fail
        if not total:
            continue

        rate = ok * 100 // total
        icon = "🟢" if rate >= 90 else ("🟡" if rate >= 60 else "🔴")
        avg = f" · {spent / ok:.1f}s" if ok and spent else ""
        streak_txt = f" ⚠️{streak}" if streak >= 3 else ""
        lines.append(f"{icon} <b>{p}</b> — {rate}% ({ok}/{total}){avg}{streak_txt}")

    grand = total_ok + total_fail
    if grand:
        lines.append(
            f"\n<b>Jami:</b> {total_ok}/{grand} "
            f"({total_ok * 100 // grand}% muvaffaqiyat)"
        )
    return "\n".join(lines)


async def get_music_report() -> str:
    """Musiqa qidiruv/yuklash statistikasi"""
    r = loader.redis_client
    if not r:
        return ""
    day = _today()
    try:
        ok = int(await r.get(f"health:ok:Music:{day}") or 0)
        fail = int(await r.get(f"health:fail:Music:{day}") or 0)
        cached = int(await r.get(f"health:cachehit:{day}") or 0)
    except Exception:
        return ""
    if not (ok or fail or cached):
        return ""
    return (
        f"\n\n🎵 <b>Musiqa:</b> {ok} yuklandi, {fail} xato, "
        f"{cached} cache'dan"
    )


async def record_cache_hit():
    r = loader.redis_client
    if not r:
        return
    try:
        day = _today()
        await r.incr(f"health:cachehit:{day}")
        await r.expire(f"health:cachehit:{day}", _TTL)
    except Exception:
        pass
