"""Cache manager works without Redis when REDIS_ENABLED=false."""

import pytest

from cache.manager import CacheManager
from config.settings import get_settings


@pytest.mark.asyncio
async def test_cache_memory_only_when_redis_disabled(monkeypatch):
    monkeypatch.setenv("REDIS_ENABLED", "false")
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()

    cache = CacheManager()
    await cache.connect()
    await cache.set("k", {"x": 1}, ttl=60)
    assert await cache.get("k") == {"x": 1}
    await cache.close()
    get_settings.cache_clear()
