"""Cache layer with Redis and in-memory fallback."""

from cache.manager import CacheManager, get_cache

__all__ = ["CacheManager", "get_cache"]
