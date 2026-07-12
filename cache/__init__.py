"""Cache layer with in-memory fallback and optional Redis."""

from cache.manager import CacheManager, get_cache

__all__ = ["CacheManager", "get_cache"]
