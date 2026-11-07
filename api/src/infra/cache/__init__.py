"""缓存驱动集。

新增实现（例如 Redis）= 在新文件里暴露 `Driver`，再改 `CACHE_DRIVER`。
和 storage / queue 同一套约定，见 `infra/loader.py`。
"""

from __future__ import annotations

from infra.cache.base import (
    Cache,
    CacheError,
    cache_get_json,
    cache_key,
    cache_set_json,
)
from infra.cache.memory import CacheSettings, MemoryCache, get_cache_settings

__all__ = [
    "Cache",
    "CacheError",
    "CacheSettings",
    "MemoryCache",
    "cache_get_json",
    "cache_key",
    "cache_set_json",
    "get_cache_settings",
]
