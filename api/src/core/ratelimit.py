"""请求限流。

## 为什么建在缓存层之上，而不是进程内计数器

限流的意义是「保护后端」，而保护对象（数据库、供应商配额、worker 槽位）是**全集群一份**。
进程内计数器在多副本下等于把额度乘以实例数：`每 IP 每分钟 60 次` 变成 60×N。
所以计数落 `CACHE_DRIVER`，现在默认内存（单实例正确），换成 Redis 之后自动全局一致。

## 扩展入口（这也是「不引 Redis 但留口子」的落点）

本文件只依赖一个 `Counter` 协议——一个原子的 `incr(key, delta, ttl)`：

- 现在：`infra/cache/memory.py`
- 以后：`infra/cache/redis.py`，只改 `CACHE_DRIVER=redis`
- 限流代码、路由代码、配置**一行都不用改**

刻意不依赖完整的 `Cache` 协议：限流不需要 `get`/`set`/`clear`，按消费方裁剪
（与 `core/ports/accounts.py` 只要求一个方法同一个理由）。

## 为什么是固定窗口而不是滑动窗口

固定窗口的实现是「一个 key 一次 `incr`」，天然原子、无锁；滑动窗口要 `ZADD` +
`ZREMRANGEBYSCORE` + `ZCARD` 三次调用，要么用 Lua 保证原子，要么就得接受计数偏差。
固定窗口的代价是**边界双倍**：窗口最后 1 秒用满、下一秒又是新窗口，瞬时可以到 2×limit。
对于「防止某个用户把队列打满」这个目的，2 倍误差可以接受；要精确限速再换滑动窗口，
届时同样只需替换 `Counter` 的实现，不必改调用方。

## 为什么默认放行（fail-open）

缓存故障时如果 fail-closed，一次 Redis 抖动就会让全站 429——**限流器自己成了故障源**。
这里选择放行并打一条限流日志（同一分钟内只打一次，避免刷屏）。这条选择是刻意的：
限流是「保护措施」，不是「鉴权」。鉴权必须 fail-closed（参见 `core/ports/assets.py`），
两者的判断标准不同：放过的代价是「可能被打一下」，拦错的代价是「所有人不能用」。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, runtime_checkable

from core.config import settings
from core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_WINDOW_SEC = 60.0

# 降级日志的节流时间戳（秒）。模块级状态够用：它只影响日志频率
_last_degrade_warning = 0.0


@runtime_checkable
class Counter(Protocol):
    """限流需要的全部能力：一次原子自增。"""

    async def incr(self, key: str, delta: int = 1, *, ttl: float | None = None) -> int: ...


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    limit: int
    used: int
    remaining: int
    # 距离窗口重置还有多久（秒），用来填 Retry-After / 提示文案
    retry_after: float
    # 计数不可用（缓存故障）时降级放行；调用方可以据此打日志或告警
    degraded: bool = False


class RateLimiter:
    def __init__(self, counter: Counter, *, prefix: str = "") -> None:
        self._counter = counter
        self._prefix = prefix.strip()

    async def hit(
        self,
        subject: str,
        *,
        scope: str,
        limit: int,
        window: float = DEFAULT_WINDOW_SEC,
        cost: int = 1,
    ) -> RateDecision:
        """给 `subject` 在 `scope` 上记一次，返回是否放行。

        `subject` 是**被限的人**（用户 id 或客户端 IP），`scope` 是**被限的动作**
        （`task.create`）。两者分开是因为同一个用户在不同接口上的合理频率完全不同。
        """
        if limit <= 0:
            # 0 / 负数 = 该接口不限流（显式关闭，而不是「限额为零」这种容易误读的语义）
            return RateDecision(True, 0, 0, 0, 0.0)
        if not scope:
            raise ValueError("限流 scope 不能为空")
        if ":" in scope:
            # scope 是代码里的字面量，写错就该在启动/测试期炸掉，不要静默改写
            raise ValueError(f"限流 scope 不能包含冒号：{scope!r}")

        window = max(1.0, float(window))
        now = time.time()
        bucket = int(now // window)
        key = ":".join(
            part
            for part in (self._prefix, "rl", scope, _slot(subject), str(bucket))
            if part
        )
        try:
            # ttl 只在新键创建时生效（见 Cache.incr 的约定），窗口才能准时归零。
            # ×2 是给「窗口末尾写入」留一格冗余，避免刚写就过期导致计数偏小。
            used = await self._counter.incr(key, cost, ttl=window * 2)
        except Exception as exc:  # noqa: BLE001 - 见模块文档：降级放行
            _warn_degraded(exc)
            return RateDecision(True, limit, 0, limit, 0.0, degraded=True)

        reset_at = (bucket + 1) * window
        return RateDecision(
            allowed=used <= limit,
            limit=limit,
            used=used,
            remaining=max(0, limit - used),
            retry_after=max(0.0, reset_at - now),
        )


def _slot(subject: str) -> str:
    """把 subject 变成安全的键片段。

    IPv6 地址自带冒号（`2001:db8::1`），直接拼进键会把「一段」变成「多段」，
    极端情况下与别人的键空间重叠。这里把冒号换成连字符——同一个人仍然稳定映射到
    同一个键，不同人撞键的概率可以忽略。
    """
    cleaned = subject.replace(":", "-").replace(" ", "_").strip()
    return cleaned or "anonymous"


def retry_after_seconds(decision: RateDecision) -> int:
    """给用户看的整数秒（向上取整，避免「请 0 秒后再试」）。"""
    return max(1, math.ceil(decision.retry_after))


def _warn_degraded(exc: Exception) -> None:
    global _last_degrade_warning
    now = time.time()
    if now - _last_degrade_warning < 60:
        return
    _last_degrade_warning = now
    logger.warning("限流计数不可用，本次降级放行：%s: %s", type(exc).__name__, exc)


@lru_cache
def get_rate_limiter() -> RateLimiter:
    """进程级限流器。

    延迟 import `infra`：`core` 不该在模块级依赖基础设施工厂（`infra` 反过来
    依赖 `core.config`），只有真正要计数时才去拿实现。
    """
    from infra import get_cache

    return RateLimiter(get_cache(), prefix=settings.cache_key_prefix)


def reset_rate_limiter() -> None:
    """仅测试使用：让下一次 `get_rate_limiter()` 重新按配置取缓存实现。"""
    get_rate_limiter.cache_clear()


__all__ = [
    "DEFAULT_WINDOW_SEC",
    "Counter",
    "RateDecision",
    "RateLimiter",
    "get_rate_limiter",
    "reset_rate_limiter",
    "retry_after_seconds",
]
