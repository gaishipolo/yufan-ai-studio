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
        """自增；**值不可解析时按 0 重来**，不抛。

        与 `read()` 同一个错误条件，必须同一个处置 —— `MemoryCache.incr` 遇到
        非整数值会抛 `ValueError`，而 `RateLimiter` 捕到异常就**降级放行**。
        于是一个被污染的键会让「这个主体 × 这个 scope」的限流**永久失效**
        （脏值不会自己变好，要等 2×window 的 TTL 才过期），与这个文件头写的
        「只让这一个键上的历史用量归零」正好相反。

        处置方式：删掉脏值再自增（从 0 起算）。这样既没有放行，也没有把异常
        抛给上层。
        """
        try:
            return await self._cache.incr(key, delta, ttl=ttl)
        except ValueError:
            # 脏值：删掉，按 0 重新开始。删也可能失败（缓存不可用），
            # 那就让异常继续冒到限流器的降级分支 —— 那时是「整个缓存挂了」，
            # 放行是既定的降级策略，与「单个键被污染」不是一回事。
            await self._cache.delete(key)
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
