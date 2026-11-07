"""限流：窗口计数、边界、降级放行。

这里全部用桩计数器 / 内存缓存，不打数据库：限流的正确性与持久化无关，
`Cache` 的实现在 `tests/test_cache.py` 里单独验。
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from api.deps import rate_limit
from core.ratelimit import RateDecision, RateLimiter, retry_after_seconds
from infra.cache.memory import MemoryCache


def _limiter() -> RateLimiter:
    return RateLimiter(MemoryCache(), prefix="test")


def test_hits_within_limit_are_allowed() -> None:
    import asyncio

    limiter = _limiter()

    async def scenario() -> list[RateDecision]:
        return [
            await limiter.hit("u1", scope="task.create", limit=3, window=60)
            for _ in range(3)
        ]

    decisions = asyncio.run(scenario())
    assert [d.allowed for d in decisions] == [True, True, True]
    assert [d.remaining for d in decisions] == [2, 1, 0]


def test_over_limit_is_rejected_with_retry_after() -> None:
    import asyncio

    limiter = _limiter()

    async def scenario() -> RateDecision:
        for _ in range(3):
            await limiter.hit("u1", scope="task.create", limit=3, window=60)
        return await limiter.hit("u1", scope="task.create", limit=3, window=60)

    decision = asyncio.run(scenario())
    assert decision.allowed is False
    assert decision.used == 4
    assert decision.remaining == 0
    assert 0 <= decision.retry_after <= 60
    assert retry_after_seconds(decision) >= 1


def test_subjects_and_scopes_do_not_share_buckets() -> None:
    """两个人、两个接口互不占用额度——否则「别人把额度用完」会变成常见故障。"""
    import asyncio

    limiter = _limiter()

    async def scenario() -> tuple[RateDecision, RateDecision]:
        await limiter.hit("u1", scope="task.create", limit=1, window=60)
        other_user = await limiter.hit("u2", scope="task.create", limit=1, window=60)
        other_scope = await limiter.hit("u1", scope="task.retry", limit=1, window=60)
        return other_user, other_scope

    other_user, other_scope = asyncio.run(scenario())
    assert other_user.allowed is True
    assert other_scope.allowed is True


def test_limit_zero_disables_limiting() -> None:
    import asyncio

    limiter = _limiter()

    async def scenario() -> list[RateDecision]:
        return [
            await limiter.hit("u1", scope="task.create", limit=0) for _ in range(5)
        ]

    decisions = asyncio.run(scenario())
    assert all(d.allowed for d in decisions)
    assert all(d.limit == 0 for d in decisions)


def test_scope_with_colon_is_rejected() -> None:
    """scope 是代码字面量：写错了要在启动/测试期炸，不要静默改写键空间。"""
    import asyncio

    with pytest.raises(ValueError, match="冒号"):
        asyncio.run(_limiter().hit("u1", scope="task:create", limit=1))


def test_ipv6_subject_does_not_escape_key_space() -> None:
    """IPv6 自带冒号：清洗后必须仍然是「一个片段」，且两人不撞键。"""
    import asyncio

    limiter = _limiter()

    async def scenario() -> tuple[RateDecision, RateDecision]:
        await limiter.hit("2001:db8::1", scope="task.create", limit=1, window=60)
        same = await limiter.hit("2001:db8::1", scope="task.create", limit=1, window=60)
        other = await limiter.hit("2001:db8::2", scope="task.create", limit=1, window=60)
        return same, other

    same, other = asyncio.run(scenario())
    assert same.allowed is False  # 同一个人：第二次应当被拦
    assert other.allowed is True  # 另一个人：不受影响


def test_window_resets_after_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    import core.ratelimit as module

    clock = {"now": 1000.0}
    monkeypatch.setattr(module.time, "time", lambda: clock["now"])
    limiter = _limiter()

    async def scenario() -> tuple[RateDecision, RateDecision, RateDecision]:
        first = await limiter.hit("u1", scope="task.create", limit=1, window=60)
        blocked = await limiter.hit("u1", scope="task.create", limit=1, window=60)
        clock["now"] += 60.0  # 跳到下一个窗口
        next_window = await limiter.hit("u1", scope="task.create", limit=1, window=60)
        return first, blocked, next_window

    first, blocked, next_window = asyncio.run(scenario())
    assert first.allowed is True
    assert blocked.allowed is False
    assert next_window.allowed is True


def test_cache_failure_degrades_to_allow() -> None:
    """缓存故障必须放行：限流器自己不能变成故障源。"""

    class BrokenCounter:
        async def incr(self, key: str, delta: int = 1, *, ttl: float | None = None) -> int:
            raise RuntimeError("redis down")

    import asyncio

    decision = asyncio.run(
        RateLimiter(BrokenCounter()).hit("u1", scope="task.create", limit=1)
    )
    assert decision.allowed is True
    assert decision.degraded is True


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "client": ("203.0.113.9", 50000),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def test_dependency_raises_429_after_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """依赖层：超过额度抛 `RateLimited`，并带上可倒计时的 `retryAfter`。"""
    import asyncio

    import api.deps as deps
    from core.context import CurrentUser
    from core.exceptions import RateLimited

    dependency = rate_limit("demo.action", limit=1)
    actor = CurrentUser(id="u-limit", name="x", roles=(), permissions=frozenset())

    # 换成独立的限流器（**同一个实例**：每次新建会让计数永远从头开始）
    limiter = RateLimiter(MemoryCache())
    monkeypatch.setattr(deps, "get_rate_limiter", lambda: limiter)

    async def scenario() -> None:
        await dependency(_request(), actor)  # 第一次放行
        with pytest.raises(RateLimited) as caught:
            await dependency(_request(), actor)
        assert caught.value.http_status == 429
        assert caught.value.details is not None
        assert caught.value.details["retryAfter"] >= 1

    asyncio.run(scenario())


def test_limit_zero_returns_noop_dependency() -> None:
    dependency = rate_limit("demo.off", limit=0)
    assert dependency.__name__ == "_noop"


def test_create_task_route_is_wired_to_rate_limit() -> None:
    """路由真的挂上了闸门——否则「有限流能力」只是一句声明。"""
    from modules.task.router import router

    route = next(
        r
        for r in router.routes
        if getattr(r, "path", None) == "/tasks" and "POST" in getattr(r, "methods", set())
    )
    names = {dep.call.__name__ for dep in route.dependant.dependencies if dep.call}
    assert "rate_limit_task_create" in names
