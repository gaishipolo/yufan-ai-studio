"""缓存抽象。

## 这个协议只解决一件事：把「算得慢/查得贵」的结果暂存一段时间

它**不是**第二份数据库。判断标准只有一条：

> 整块缓存丢失后，业务是否仍然正确？

- 会 → 可以放（模型清单、热点只读聚合、限流计数）；
- 不会 → 不许放（会话、令牌、余额、任务状态、归档索引）。

会话与令牌尤其要小心：权威状态在 `sessions` 表，缓存只做加速。否则多实例部署时
「A 登录、B 不认」，而重启一台机器就等于全体掉线。

## 为什么只有 bytes

和 `ObjectStorage.put(key, data: bytes)` 同一个理由：编码是**消费方的选择**，
存储层替它决定 utf-8 还是 msgpack，换实现时就会出现「本地能读、Redis 读出乱码」。
嫌麻烦用 `cache_get_json` / `cache_set_json`，那是显式的约定，不是隐式魔法。

## 为什么没有 get_or_set

「缓存未命中就回源」看起来方便，但它把**回源策略**（要不要加锁、要不要返回旧值、
失败是否降级）塞进了存储层。那是消费方的决策，不同业务答案不同。这里只提供
`get` / `set` / `incr` 三个原子动作，回源顺序由调用方写清楚。

## 为什么没有锁

`incr` 必须原子（限流计数靠它），但**不需要分布式锁**：`incr` 本身就是原子的。
真正的「只允许一个协程回源」需要 singleflight，属消费方策略（见上一节）。
"""
from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from core.config import settings


class CacheError(Exception):
    """缓存层异常。缓存故障默认应当降级，不要把异常抛给用户。"""


@runtime_checkable
class Cache(Protocol):
    """最小缓存接口。TTL 单位是秒，`None` 表示不过期。"""

    async def get(self, key: str) -> bytes | None:
        """读取；不存在或已过期返回 `None`（不要抛异常）。"""
        ...

    async def set(self, key: str, value: bytes, *, ttl: float | None = None) -> None:
        """写入并设置过期时间；`ttl<=0` 视为立即过期（等于删除）。"""
        ...

    async def delete(self, key: str) -> None:
        """删除；键不存在时幂等返回。"""
        ...

    async def exists(self, key: str) -> bool:
        """是否存在且未过期。已过期的键必须返回 `False`，不能泄漏「曾经存在」。"""
        ...

    async def incr(self, key: str, delta: int = 1, *, ttl: float | None = None) -> int:
        """原子自增并返回新值。

        键不存在时从 0 起算（返回 `delta`）。`ttl` **只在新键创建时生效**：
        每次自增都重置过期时间会让「每分钟 100 次」变成永不重置的计数器。
        """
        ...

    async def clear(self) -> None:
        """清空当前命名空间。仅测试与运维使用。"""
        ...


def cache_key(*parts: str) -> str:
    """拼一个带部署前缀的键，例如 `cache_key("model", "list")`。

    前缀来自 `CACHE_KEY_PREFIX`：多个环境共用一个 Redis 时靠它隔离，避免
    dev 的脏数据把 prod 打穿。片段里**不允许冒号**，否则一个 `"a:b"` 就能
    伪造出别人的键空间（例如 `cache_key("limit", "a:b")` 覆盖 `limit:a:b`）。
    """
    segments: list[str] = []
    for part in parts:
        if not part:
            raise ValueError("缓存键片段不能为空")
        if ":" in part:
            raise ValueError(f"缓存键片段不能包含冒号：{part!r}")
        segments.append(part)
    prefix = settings.cache_key_prefix.strip()
    return ":".join([prefix, *segments]) if prefix else ":".join(segments)


async def cache_get_json(cache: Cache, key: str) -> Any | None:
    """读 JSON。内容损坏时返回 `None` 而不是抛异常。

    损坏只可能来自「旧版本写过不兼容结构」或「别人手动改过值」，
    这两种情况回源重建都是正确做法，为它抛异常只会把一次缓存失效变成一次 500。
    """
    raw = await cache.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        await cache.delete(key)
        return None


async def cache_set_json(
    cache: Cache, key: str, value: Any, *, ttl: float | None = None
) -> None:
    """写 JSON。`ensure_ascii=False` 让中文在 redis-cli 里可读，排查时省一次猜测。"""
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    await cache.set(key, payload, ttl=ttl)


__all__ = [
    "Cache",
    "CacheError",
    "cache_get_json",
    "cache_key",
    "cache_set_json",
]
