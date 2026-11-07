"""进程内缓存驱动。

它是**默认实现**，不是玩具：单实例部署下够用，行为也定义了协议的全部语义
（TTL、LRU 淘汰、原子自增）。换成 Redis 时消费方代码一行不用改。

## 边界（写在这里，因为出错时现象很隐蔽）

- **进程内**：两个副本各存一份，`incr` 计数会翻倍、`delete` 只影响本进程。
  所以它只能放「每个实例各算各的也无所谓」的数据。限流计数如果要全局限流，
  必须换共享实现——单机 quota 与全局 quota 是两个不同的产品语义。
- **重启即空**：这是特性不是缺陷。缓存里不该有「丢了就错」的数据（见 `base.py`）。
- **不跨 event loop 加锁**：所有方法在一次 `await` 之前完成同步读写，单循环下天然原子。
  不用 `asyncio.Lock` 是因为它绑定创建时的 loop，而 CLI / 测试里每个 `asyncio.run`
  都会新建 loop，复用一个带锁的实例会直接抛「bound to a different event loop」。
"""
from __future__ import annotations

import time
from collections import OrderedDict
from functools import lru_cache

from infra.driver_config import DriverSettings, driver_config


class CacheSettings(DriverSettings):
    """内存缓存的实现参数。"""

    model_config = driver_config("CACHE")

    driver_kind = "cache"
    driver_name = "memory"

    # 条目上限。超限先清过期，再按 LRU 淘汰最久未使用的。
    # 不设 0：无上限的进程内缓存就是一个缓慢的内存泄漏。
    max_entries: int = 10_000


@lru_cache
def get_cache_settings() -> CacheSettings:
    return CacheSettings()


class MemoryCache:
    """`Cache` 协议的内存实现（LRU + TTL）。"""

    def __init__(self, config: CacheSettings | None = None) -> None:
        self._config = config or get_cache_settings()
        self._data: OrderedDict[str, tuple[bytes, float | None]] = OrderedDict()

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _now() -> float:
        # monotonic：系统时钟被 NTP 校准或手动改动时，TTL 不会突然全部失效或永不过期
        return time.monotonic()

    def _alive(self, key: str) -> tuple[bytes, float | None] | None:
        """取未过期的条目；过期则顺手删掉（惰性过期，不跑后台清理协程）。"""
        item = self._data.get(key)
        if item is None:
            return None
        expires_at = item[1]
        if expires_at is not None and expires_at <= self._now():
            del self._data[key]
            return None
        return item

    def _evict(self) -> None:
        limit = max(1, self._config.max_entries)
        if len(self._data) <= limit:
            return
        # 先清已过期的（它们本来就不算有效条目），再按 LRU 淘汰
        now = self._now()
        expired = [
            key
            for key, (_value, expires_at) in self._data.items()
            if expires_at is not None and expires_at <= now
        ]
        for key in expired:
            self._data.pop(key, None)
        while len(self._data) > limit:
            self._data.popitem(last=False)

    # ------------------------------------------------------------------ 协议
    async def get(self, key: str) -> bytes | None:
        item = self._alive(key)
        if item is None:
            return None
        self._data.move_to_end(key)
        return item[0]

    async def set(self, key: str, value: bytes, *, ttl: float | None = None) -> None:
        if not isinstance(value, bytes):
            raise TypeError("缓存值必须是 bytes；要存结构化数据用 cache_set_json")
        if ttl is not None and ttl <= 0:
            self._data.pop(key, None)
            return
        expires_at = None if ttl is None else self._now() + ttl
        self._data[key] = (value, expires_at)
        self._data.move_to_end(key)
        self._evict()

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def exists(self, key: str) -> bool:
        return self._alive(key) is not None

    async def incr(self, key: str, delta: int = 1, *, ttl: float | None = None) -> int:
        item = self._alive(key)
        if item is None:
            current = 0
            expires_at = None if ttl is None else self._now() + ttl
        else:
            try:
                current = int(item[0])
            except ValueError as exc:
                raise ValueError(
                    f"缓存键 {key!r} 的值不是整数，无法自增；先 delete 再用 incr"
                ) from exc
            # 已存在的键保留原过期时间：每次 incr 都续期会让窗口计数永不归零
            expires_at = item[1]
        new_value = current + delta
        self._data[key] = (str(new_value).encode("ascii"), expires_at)
        self._data.move_to_end(key)
        self._evict()
        return new_value

    async def clear(self) -> None:
        self._data.clear()

    # ------------------------------------------------------------------ 诊断
    @property
    def size(self) -> int:
        """当前有效条目数（不含已过期未清理的）。"""
        now = self._now()
        return sum(
            1
            for _value, expires_at in self._data.values()
            if expires_at is None or expires_at > now
        )


Driver = MemoryCache


__all__ = ["CacheSettings", "MemoryCache", "get_cache_settings"]
