"""
Redis clients — sync for the (sync) REST endpoints, async for the
broadcaster task and WebSocket handlers.
"""

from functools import lru_cache

import redis
import redis.asyncio as aioredis

from app.config import settings


@lru_cache
def get_redis() -> redis.Redis:
    """Sync client — used inside sync endpoints (HSETNX/ZADD are sub-ms)."""
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


@lru_cache
def get_async_redis() -> aioredis.Redis:
    """Async client — used by the snapshot broadcaster and WS handlers."""
    return aioredis.Redis.from_url(settings.redis_url, decode_responses=True)


async def close_redis() -> None:
    get_redis().close()
    await get_async_redis().aclose()
