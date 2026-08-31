"""
Vaqtinchalik state uchun Redis do'koni (bot restart bo'lsa yo'qolmaydi).

Redis ulanmagan bo'lsa avtomatik xotiradagi dict ga tushadi — bot baribir ishlaydi.
"""
import json
import logging
import time

import loader

logger = logging.getLogger(__name__)

# Redis bo'lmasa zaxira
_memory = {}
_memory_exp = {}


def _gc():
    now = time.time()
    for k, exp in list(_memory_exp.items()):
        if exp < now:
            _memory.pop(k, None)
            _memory_exp.pop(k, None)


async def set_state(key: str, value, ttl: int = 1800):
    r = loader.redis_client
    if r:
        try:
            await r.set(f"st:{key}", json.dumps(value), ex=ttl)
            return
        except Exception as e:
            logger.debug(f"[state] set xato: {e}")
    _gc()
    _memory[key] = value
    _memory_exp[key] = time.time() + ttl


async def get_state(key: str, default=None):
    r = loader.redis_client
    if r:
        try:
            raw = await r.get(f"st:{key}")
            if raw:
                return json.loads(raw)
            return default
        except Exception as e:
            logger.debug(f"[state] get xato: {e}")
    _gc()
    return _memory.get(key, default)


async def pop_state(key: str, default=None):
    r = loader.redis_client
    if r:
        try:
            raw = await r.get(f"st:{key}")
            await r.delete(f"st:{key}")
            if raw:
                return json.loads(raw)
            return default
        except Exception as e:
            logger.debug(f"[state] pop xato: {e}")
    _gc()
    _memory_exp.pop(key, None)
    return _memory.pop(key, default)


async def delete_state(key: str):
    r = loader.redis_client
    if r:
        try:
            await r.delete(f"st:{key}")
            return
        except Exception:
            pass
    _memory.pop(key, None)
    _memory_exp.pop(key, None)
