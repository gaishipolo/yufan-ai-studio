"""请求限流。

## 为什么建在缓存层之上，而不是进程内计数器

限流的意义是「保护后端」，而保护对象（数据库、供应商配额、worker 槽位）是**全集群一份**。
进程内计数器在多副本下等于把额度乘以实例数：`每 IP 每分钟 60 次` 变成 60×N。
所以计数落 `CACHE_DRIVER`，现在默认内存（单实例正确），换成 Redis 之后自动全局一致。

## 扩展入口（这也是「不引 Redis 但留口子」的落点）

本文件只依赖一个 `Counter` 协议——两个原子动作：`incr(key, delta, ttl)` 和
`read(key)`：

- 现在：`infra/cache/counter.py`（包在 memory 缓存外）
- 以后：换成 Redis 实现，只改 `CACHE_DRIVER`，限流代码、路由代码、配置都不用动

刻意不依赖完整的 `Cache` 协议：限流不需要 `set`/`delete`/`clear`，按消费方裁剪
（与 `core/ports/accounts.py` 只要求一个方法同一个理由）。

## 为什么是「两个桶加权」而不是固定窗口

固定窗口是「一个 key 一次 `incr`」，最省事，但代价明确：窗口最后 1 秒打满、
过了整点又是新窗口——**瞬时可以到 2×limit**，且这个 2 倍不是理论值，掐着表就能刷出来。

滑动窗口的教科书实现是 `ZADD` + `ZREMRANGEBYSCORE` + `ZCARD`，但那是 Redis 专有命令，
一旦写进核心逻辑，「以后换驱动」这个前提就没了。

这里用的是两者的中间方案——**sliding window counter**（Cloudflare 的做法）：
键仍然是「一个窗口一个计数」，但判断时**同时读上一窗口的计数，并按本窗口已过去
的比例线性衰减**：

    estimate = curr + prev × (1 − 已过比例)

- `p→0`（刚跨窗口）：`estimate ≈ prev`，正好等于「过去 60 秒」的真实值 → 精确；
- `p→1`（窗口末尾）：`prev` 权重归零，只算本窗口 → 与真实值差「上一窗口末尾那一小段」。

于是「整份额度被重复计算」的洞被堵掉，剩下的误差是**上一窗口末端的尾巴**，
量级是「那几秒的请求数」，而不是「limit」。均匀流量下误差 < 1%。

代价：每次判断多一次 `read`（一次 GET），且必须保证上一窗口的键在下一整个窗口内
都还活着 → 本窗口的 `ttl` 设成 `2×window`（见 `hit` 里的注释）。

## 为什么被拒的请求不消耗额度

`hit` 先自增再判断，这是原子的（一次 `incr` 拿返回值即可），代价是被拒的那次也
计数了。如果就这么放着，「照着 `retryAfter` 重试」会让你越试越超——额度低的接口
（比如 5 次/分钟）甚至永远出不来，因为每次重试都往桶里加 1，而衰减速度只有 0.08/秒。

所以被拒时显式回滚（`incr(key, -cost)`）。两次 `incr` 之间可能有并发，但误差只是
「同时被拒的次数 × cost」，方向是**多减**——最坏结果是一次误放行，不会变成永久封禁。
这个方向是刻意选的：限流是保护措施，宁可少拦，不可错杀。

## 额度：写死在路由上的是「基线」，不是最终值

`rate_limit("task.create", limit=30)` 里的 30 是**默认值**，运行时会被两层覆盖：

- `RATE_LIMIT_OVERRIDES`（JSON，`{"task.create": 120}`）：按 scope 单点调整；
- `RATE_LIMIT_SCALE`（浮点，默认 1.0）：整体倍率，活动期间放宽、故障时收紧，
  `0` 等于全关。

两者的先后是「先覆盖、后缩放」：覆盖定的是这个接口值多少，缩放定的是今天整体放宽多少。
反过来（先缩放后覆盖）会让 `SCALE=0` 时被覆盖过的接口还在放行——那不是「全关」。

代价是「代码里写的额度」和「线上生效的额度」可能不一致，所以：配置里出现代码里
**不存在的 scope** 会红（`unknown_overrides`，`tests/test_ratelimit.py` 里对账）。
这和豁免表同一个道理——手写的清单，不对账就等于一份看起来被审阅过的错误信息。

## 为什么默认放行（fail-open）

缓存故障时如果 fail-closed，一次 Redis 抖动就会让全站 429——**限流器自己成了故障源**。
这里选择放行并打一条限流日志（同一分钟内只打一次，避免刷屏）。这条选择是刻意的：
限流是「保护措施」，不是「鉴权」。鉴权必须 fail-closed（参见 `core/ports/assets.py`），
两者的判断标准不同：放过的代价是「可能被打一下」，拦错的代价是「所有人不能用」。
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, runtime_checkable

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_WINDOW_SEC = 60.0

# 降级日志的节流时间戳（秒）。模块级状态够用：它只影响日志频率
_last_degrade_warning = 0.0


@runtime_checkable
class Counter(Protocol):
    """限流需要的全部能力：原子自增 + 读数。"""

    async def incr(self, key: str, delta: int = 1, *, ttl: float | None = None) -> int: ...

    async def read(self, key: str) -> int:
        """读当前值；键不存在或已过期返回 0（不要抛异常）。"""
        ...


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    limit: int
    # 估算的「已用额度」：含本次（未被拒时），是滑动窗口加权的当量值
    used: int
    remaining: int
    # 距离能再放行一次还有多久（秒），用来填 Retry-After / 提示文案
    retry_after: float
    # 计数不可用（缓存故障）时降级放行；调用方可以据此打日志或告警
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class Budget:
    """一次限流判断用的额度：路由写的默认值 + 运维覆盖/缩放之后的结果。"""

    limit: int
    window: float
    cost: int

    @property
    def disabled(self) -> bool:
        return self.limit <= 0


# 代码里真正声明过的 scope。用于对账 `RATE_LIMIT_OVERRIDES` 里写错的名字。
# 只在 `rate_limit()` 被调用时填充——也就是「路由模块被导入时」。
# CLI 不走 `create_app()`，所以这里的空集**不代表没有 scope**，
# 消费方必须自己先 `discover_modules()`（这个坑踩过一次，见 docs/OPS.md §7）。
_declared_scopes: set[str] = set()


def register_scope(scope: str) -> None:
    _declared_scopes.add(scope)


def declared_scopes() -> frozenset[str]:
    return frozenset(_declared_scopes)


@lru_cache
def parsed_overrides() -> Mapping[str, int]:
    """解析 `RATE_LIMIT_OVERRIDES`。写错就炸，不要静默忽略一条配置。"""
    raw = get_settings().rate_limit_overrides.strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"RATE_LIMIT_OVERRIDES 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError('RATE_LIMIT_OVERRIDES 必须是 {"scope": 每分钟次数} 形式的对象')
    parsed: dict[str, int] = {}
    for scope, limit in data.items():
        if not scope or ":" in scope:
            raise ValueError(f"RATE_LIMIT_OVERRIDES 里的 scope 非法：{scope!r}")
        # bool 是 int 的子类，`{"x": true}` 会静默变成 1 次/分钟——这种静默最容易上线
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError(
                f"RATE_LIMIT_OVERRIDES[{scope!r}] 必须是非负整数，实际是 {limit!r}"
            )
        parsed[scope] = limit
    return parsed


def resolve_budget(
    scope: str,
    *,
    limit: int,
    window: float = DEFAULT_WINDOW_SEC,
    cost: int = 1,
) -> Budget:
    """把「路由上写的额度」换算成「这次真正要用的额度」。

    两个下界都在这里收，别指望调用方记住：

    1. **缩放不能把额度算成 0**。`round(10 * 0.05) == 0`，而 `limit == 0` 的语义是
       「该接口不限流」——故障期把 `RATE_LIMIT_SCALE` 调小本来是为了收紧，算成 0
       却变成全站放行，配置写的和实际生效的正好相反。真正「全关」只有
       `SCALE == 0` 这一个入口（见模块文档），任何正数倍率都至少留 1 次。
    2. **额度不能小于单次消耗**。`hit()` 判的是 `estimate <= limit`，而
       `estimate` 至少是 `cost`：limit=1、cost=2 时每次必 429，`retry_after`
       永远指着窗口边界，照着它重试也不可能成功。抬到刚好放行一次。
    """
    effective = parsed_overrides().get(scope, limit)
    cost = max(1, int(cost))
    scale = get_settings().rate_limit_scale
    if scale == 0:
        # 唯一的「整体关闸」入口：显式写成 0，不是算出来的 0
        effective = 0
    elif scale != 1.0 and effective > 0:
        effective = max(1, round(effective * scale))
    if 0 < effective < cost:
        logger.warning(
            "限流额度 %s 小于单次消耗 %s，按「刚好放行一次」处理（scope=%s）",
            effective,
            cost,
            scope,
        )
        effective = cost
    return Budget(limit=int(effective), window=window, cost=cost)


def unknown_overrides(known: Iterable[str]) -> list[str]:
    """配置里有、代码里没有的 scope。测试用：手写清单必须对得上账。"""
    return sorted(set(parsed_overrides()) - set(known))


def reset_rate_limits() -> None:
    """仅测试使用：清掉配置解析缓存，让下一个人按新的 settings 重算。"""
    parsed_overrides.cache_clear()


class RateLimiter:
    def __init__(self, counter: Counter, *, prefix: str = "") -> None:
        # 直接传 `Cache` 会缺 `read`，而缺方法的表现是「每次 hit 抛 AttributeError
        # → 降级放行」——限流静默失效，只留一条 warning。那是排查起来最费劲的一类故障，
        # 所以在构造时就炸。`Cache` 要先用 `infra.cache.counter.CacheCounter` 包一层。
        if not isinstance(counter, Counter):
            raise TypeError(
                "RateLimiter 需要 Counter 协议（incr + read）；"
                f"{type(counter).__name__} 不满足，"
                "缓存实现请先用 infra.cache.counter.CacheCounter 适配"
            )
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
        cost = max(1, int(cost))
        now = time.time()
        bucket = int(now // window)
        start = bucket * window
        elapsed = now - start

        base = self._key(scope, subject, limit=limit, window=window)
        curr_key = f"{base}:{bucket}"
        prev_key = f"{base}:{bucket - 1}"
        try:
            # 先读上一窗口再自增本窗口：两个键不同，先后无关。
            # ttl 只在新键创建时生效（见 Cache.incr 的约定），设成 2×window
            # 是为了让这个键在**下一整个窗口**里还活着——下一窗口要靠它当 prev。
            prev = await self._counter.read(prev_key)
            used = await self._counter.incr(curr_key, cost, ttl=window * 2)
        except Exception as exc:  # noqa: BLE001 - 见模块文档：降级放行
            _warn_degraded(exc)
            return RateDecision(True, limit, 0, limit, 0.0, degraded=True)

        # 上一窗口的当量：越接近本窗口末尾，权重越低
        carried = prev * max(0.0, 1.0 - elapsed / window)
        estimate = used + carried
        allowed = estimate <= limit

        if allowed:
            quantized = math.ceil(estimate)
            return RateDecision(
                allowed=True,
                limit=limit,
                used=quantized,
                remaining=max(0, limit - quantized),
                retry_after=0.0,
            )

        # 超了：把本次加进去的那一份退回来（见模块文档「为什么被拒的请求不消耗额度」）
        try:
            await self._counter.incr(curr_key, -cost, ttl=window * 2)
        except Exception as exc:  # noqa: BLE001 - 回滚失败只影响精度，不该让请求变成 500
            _warn_degraded(exc)
        estimate -= cost
        quantized = math.ceil(estimate)
        return RateDecision(
            allowed=False,
            limit=limit,
            used=quantized,
            remaining=max(0, limit - quantized),
            retry_after=self._wait_hint(
                now=now,
                start=start,
                window=window,
                needed=max(0, limit - cost),
                prev=prev,
                curr=max(0, used - cost),
            ),
        )

    def _key(self, scope: str, subject: str, *, limit: int, window: float) -> str:
        """桶 key：`前缀:rl:scope:主体:额度@窗口:窗口序号`。

        **额度必须进 key**。同一个 scope 挂多档额度是真实存在的
        （`library.trash` 的 restore 60/min、purge 30/min），而 scope 本身又会被
        复用（`library.share` 四个入口共用 60/min，这是有意的设计）。只按
        scope 分桶的话，前者会变成「两条路由抢一份额度」，实际额度取决于请求顺序，
        而且拒绝时返回的 `remaining` 是按当前路由的 limit 算的——数字对不上是真事。

        代价是改额度（含 `RATE_LIMIT_SCALE`、`RATE_LIMIT_OVERRIDES`）会让在途的
        桶换到新键，等价于「改配置即重置窗口」。这对限流是对的：新额度应该立刻
        按新额度算，而不是先继承一段旧窗口的计数。
        """
        return ":".join(
            part
            for part in (
                self._prefix,
                "rl",
                scope,
                _slot(subject),
                f"{limit}@{window:g}",
            )
            if part
        )

    @staticmethod
    def _wait_hint(
        *,
        now: float,
        start: float,
        window: float,
        needed: int,
        prev: int,
        curr: int,
    ) -> float:
        """估算「还要多久才能再放行一次」（秒）。

        滑动窗口的返回值不再是「到下一个整点」这类固定值，而是解出来的：
        当量要降到 `needed` 以下才放行，而两段的下降速率不同。

        - **本窗口内**：只有 `prev` 的权重在降，速率 `prev / window`；
        - **跨到下一个窗口**：`curr` 整体变成上一窗口的计数，速率 `curr / window`。

        两段都解不出来（配置成 `cost > limit`）时给一个窗口的保守值，
        让调用方至少不会拿到「0 秒」然后立刻又被拒。
        """
        remaining = max(0.0, start + window - now)
        elapsed = now - start

        if prev > 0:
            # 当前当量 = curr + prev*(1 - elapsed/window)，要它降到 needed
            amount = curr + prev * (1.0 - elapsed / window) - needed
            wait = amount / (prev / window)
            if wait <= remaining:
                return max(0.0, wait)

        if curr > needed:
            return remaining + (curr - needed) / (curr / window)
        # 跨过桶边界时 prev 权重归零，当量直接落到 curr：边界就是答案
        return remaining


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
    from infra.cache.counter import CacheCounter

    return RateLimiter(
        CacheCounter(get_cache()), prefix=get_settings().cache_key_prefix
    )


def reset_rate_limiter() -> None:
    """仅测试使用：让下一次 `get_rate_limiter()` 重新按配置取缓存实现。"""
    get_rate_limiter.cache_clear()


__all__ = [
    "DEFAULT_WINDOW_SEC",
    "Budget",
    "Counter",
    "RateDecision",
    "RateLimiter",
    "declared_scopes",
    "get_rate_limiter",
    "parsed_overrides",
    "register_scope",
    "reset_rate_limits",
    "reset_rate_limiter",
    "resolve_budget",
    "retry_after_seconds",
    "unknown_overrides",
]
