"""把 `Cache` 适配成限流要的计数器。

## 为什么需要这一层（而不是让 `RateLimiter` 直接吃 `Cache`）

`Cache.get` 返回 `bytes`，理由是「编码是消费方的选择」（见 `base.py`）；
而限流要的是整数。如果 `core.ratelimit` 自己去 `int(raw)`，那「memory 驱动
把计数写成 ascii 十进制」这个**实现细节**就被写进了核心逻辑——换成一个
原生计数器的驱动（Redis `INCR` 返回整数、或用 msgpack 存值）立刻失效。

这一层很薄，但它把「驱动必须提供原子自增 + 读数」变成显式契约，
`Counter` 协议因此可以只声明两个方法，而不是把整个缓存接口拖进来。

## 为什么读失败返回值是 0 而不是抛异常

`Cache.get` 拿到的值被污染（旧版本写过别的结构、有人手动改过）时，
抛异常会让限流器进入降级分支——**整站放行**。这里返回 0 更精确：
只让这一个键上的历史用量归零，其余键照常计数。
「少拦一次」和「全都不拦」是两种不同的失误，这里选前者。
"""
from __future__ import annotations

from infra.cache.base import Cache


class CacheCounter:
    """`core.ratelimit.Counter` 在缓存驱动上的实现。

    刻意不 import `core.ratelimit`：那会让 `core` ↔ `infra` 形成循环
    （`core.ratelimit` 为了取实现才延迟 import `infra`）。协议是结构化的，
    不 import 也能满足。
    """

    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    async def incr(self, key: str, delta: int = 1, *, ttl: float | None = None) -> int:
        return await self._cache.incr(key, delta, ttl=ttl)

    async def read(self, key: str) -> int:
        """读当前计数；键不存在、已过期、内容不可解析都返回 0。"""
        raw = await self._cache.get(key)
        if raw is None:
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0


__all__ = ["CacheCounter"]
