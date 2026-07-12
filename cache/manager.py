"""Async cache manager with Redis primary and memory fallback."""

import json
from typing import Any

import redis.asyncio as redis

from config.settings import get_settings
from utils.logging import get_logger

logger = get_logger(__name__)


class CacheManager:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._memory: dict[str, tuple[Any, float | None]] = {}
        self._redis: redis.Redis | None = None

    async def connect(self) -> None:
        if not self._settings.redis_enabled or not self._settings.redis_url.strip():
            logger.info("cache.memory_only")
            return
        try:
            self._redis = redis.from_url(self._settings.redis_url, decode_responses=True)
            await self._redis.ping()
            logger.info("cache.redis.connected")
        except Exception as exc:
            logger.warning("cache.redis.unavailable", error=str(exc))
            self._redis = None

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()

    async def get(self, key: str) -> Any | None:
        if self._redis:
            try:
                value = await self._redis.get(key)
                return json.loads(value) if value else None
            except Exception:
                pass
        entry = self._memory.get(key)
        return entry[0] if entry else None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        ttl = ttl or self._settings.cache_ttl_seconds
        serialized = json.dumps(value, default=str)
        if self._redis:
            try:
                await self._redis.setex(key, ttl, serialized)
                return
            except Exception:
                pass
        self._memory[key] = (value, ttl)

    async def delete(self, key: str) -> None:
        if self._redis:
            try:
                await self._redis.delete(key)
            except Exception:
                pass
        self._memory.pop(key, None)


_cache: CacheManager | None = None


async def get_cache() -> CacheManager:
    global _cache
    if _cache is None:
        _cache = CacheManager()
        await _cache.connect()
    return _cache
