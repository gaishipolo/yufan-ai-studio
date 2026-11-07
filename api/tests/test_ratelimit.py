"""限流：两个桶加权、边界、拒绝后回滚、降级放行。

这里全部用桩计数器 / 内存缓存，不打数据库：限流的正确性与持久化无关，
`Cache` 的实现在 `tests/test_cache.py` 里单独验。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from starlette.requests import Request

from api.deps import rate_limit
from core.ratelimit import Counter, RateDecision, RateLimiter, retry_after_seconds
from infra.cache.counter import CacheCounter
from infra.cache.memory import MemoryCache


def _limiter() -> RateLimiter:
    return RateLimiter(CacheCounter(MemoryCache()), prefix="test")


def test_hits_within_limit_are_allowed() -> None:
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
    limiter = _limiter()

    async def scenario() -> RateDecision:
        for _ in range(3):
            await limiter.hit("u1", scope="task.create", limit=3, window=60)
        return await limiter.hit("u1", scope="task.create", limit=3, window=60)

    decision = asyncio.run(scenario())
    assert decision.allowed is False
    # 被拒的那次已回滚：报出去的「用了几次」不该把没执行的那次算上
    assert decision.used == 3
    assert decision.remaining == 0
    assert 0 < decision.retry_after <= 120
    assert retry_after_seconds(decision) >= 1


def test_subjects_and_scopes_do_not_share_buckets() -> None:
    """两个人、两个接口互不占用额度——否则「别人把额度用完」会变成常见故障。"""
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
    with pytest.raises(ValueError, match="冒号"):
        asyncio.run(_limiter().hit("u1", scope="task:create", limit=1))


def test_ipv6_subject_does_not_escape_key_space() -> None:
    """IPv6 自带冒号：清洗后必须仍然是「一个片段」，且两人不撞键。"""
    limiter = _limiter()

    async def scenario() -> tuple[RateDecision, RateDecision]:
        await limiter.hit("2001:db8::1", scope="task.create", limit=1, window=60)
        same = await limiter.hit("2001:db8::1", scope="task.create", limit=1, window=60)
        other = await limiter.hit("2001:db8::2", scope="task.create", limit=1, window=60)
        return same, other

    same, other = asyncio.run(scenario())
    assert same.allowed is False  # 同一个人：第二次应当被拦
    assert other.allowed is True  # 另一个人：不受影响


def test_window_resets_after_a_full_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """额度按「过去一个窗口」算，不按「整点」算：跨整点那一秒还不算清零。"""
    import core.ratelimit as module

    clock = {"now": 1000.0}
    monkeypatch.setattr(module.time, "time", lambda: clock["now"])
    limiter = _limiter()

    async def scenario() -> tuple[RateDecision, RateDecision, RateDecision]:
        first = await limiter.hit("u1", scope="task.create", limit=1, window=60)
        blocked = await limiter.hit("u1", scope="task.create", limit=1, window=60)
        clock["now"] += 120.0  # 整整一个窗口之后
        next_window = await limiter.hit("u1", scope="task.create", limit=1, window=60)
        return first, blocked, next_window

    first, blocked, next_window = asyncio.run(scenario())
    assert first.allowed is True
    assert blocked.allowed is False
    assert next_window.allowed is True


def test_window_boundary_does_not_double_the_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """固定窗口的经典漏洞：上一窗口打满，跨秒后还能再打满 → 瞬时 2×limit。

    这里把额度压在窗口最后一秒用完，然后立刻跨到下一窗口：
    加权之后的估算值仍然是满的，第三方的额度不会被凭空复制一份。
    """
    import core.ratelimit as module

    clock = {"now": 1019.0}  # 窗口 [960,1020) 的最后一秒
    monkeypatch.setattr(module.time, "time", lambda: clock["now"])
    limiter = _limiter()

    async def scenario() -> tuple[list[RateDecision], list[RateDecision]]:
        tail = [
            await limiter.hit("u1", scope="task.create", limit=5, window=60)
            for _ in range(5)
        ]
        clock["now"] = 1020.0  # 刚跨过窗口边界
        after = [
            await limiter.hit("u1", scope="task.create", limit=5, window=60)
            for _ in range(2)
        ]
        return tail, after

    tail, after = asyncio.run(scenario())
    assert [d.allowed for d in tail] == [True] * 5
    # 跨边界后不再有 5 次可用；旧实现这里会全部放行
    assert [d.allowed for d in after] == [False, False]


def test_rejected_attempt_does_not_consume_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """被拒的请求要回滚。否则「按 retryAfter 重试」会越试越超，小额度接口永远出不来。"""
    import core.ratelimit as module

    clock = {"now": 1000.0}
    monkeypatch.setattr(module.time, "time", lambda: clock["now"])
    limiter = _limiter()

    async def scenario() -> list[RateDecision]:
        return [
            await limiter.hit("u1", scope="task.create", limit=2, window=60)
            for _ in range(6)
        ]

    decisions = asyncio.run(scenario())
    assert [d.allowed for d in decisions] == [True, True, False, False, False, False]
    # 6 次里只有 2 次真执行：用量不该涨到 6
    assert [d.used for d in decisions] == [1, 2, 2, 2, 2, 2]


def test_retry_after_really_unblocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """`retryAfter` 是承诺，不是装饰：按它等完必须能放行。

    这条测试守住的是「倒计时到 0 还是 429」那个体验问题。
    """
    import core.ratelimit as module

    clock = {"now": 1000.0}
    monkeypatch.setattr(module.time, "time", lambda: clock["now"])
    limiter = _limiter()

    async def scenario() -> tuple[RateDecision, RateDecision]:
        for _ in range(2):
            await limiter.hit("u1", scope="task.create", limit=2, window=60)
        denied = await limiter.hit("u1", scope="task.create", limit=2, window=60)
        clock["now"] += retry_after_seconds(denied)
        again = await limiter.hit("u1", scope="task.create", limit=2, window=60)
        return denied, again

    denied, again = asyncio.run(scenario())
    assert denied.allowed is False
    assert again.allowed is True


def test_cache_failure_degrades_to_allow() -> None:
    """缓存故障必须放行：限流器自己不能变成故障源。"""

    class BrokenCounter:
        async def incr(self, key: str, delta: int = 1, *, ttl: float | None = None) -> int:
            raise RuntimeError("redis down")

        async def read(self, key: str) -> int:
            raise RuntimeError("redis down")

    decision = asyncio.run(
        RateLimiter(BrokenCounter()).hit("u1", scope="task.create", limit=1)
    )
    assert decision.allowed is True
    assert decision.degraded is True


def test_bare_cache_is_rejected_at_construction() -> None:
    """传 `Cache` 而没包适配层，会在构造期炸——不能让它变成「静默降级放行」。"""
    cache = MemoryCache()
    assert not isinstance(cache, Counter)
    with pytest.raises(TypeError, match="CacheCounter"):
        RateLimiter(cache)  # type: ignore[arg-type]


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
    import api.deps as deps
    from core.context import CurrentUser
    from core.exceptions import RateLimited

    dependency = rate_limit("demo.action", limit=1)
    actor = CurrentUser(id="u-limit", name="x", roles=(), permissions=frozenset())

    # 换成独立的限流器（**同一个实例**：每次新建会让计数永远从头开始）
    limiter = RateLimiter(CacheCounter(MemoryCache()))
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

# --------------------------------------------------------------------------- #
# 额度可以被运维改：覆盖、缩放、以及对账
# --------------------------------------------------------------------------- #
@pytest.fixture()
def budget_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """改 `RATE_LIMIT_*` 并清掉解析缓存；退出时一起还原。

    `_overrides()` 是 `lru_cache` 的，改了 settings 不清缓存等于没改——
    这类"改了配置但没生效"的排查最费时间，所以封在夹具里。
    """
    from core.config import settings
    from core.ratelimit import reset_rate_limits

    def apply(**fields: Any) -> None:
        for name, value in fields.items():
            monkeypatch.setattr(settings, name, value)
        reset_rate_limits()

    yield apply
    reset_rate_limits()


def _spam(dependency: Any, actor: Any, times: int) -> list[bool]:
    """连续打 N 次，返回每次是否放行。"""
    from core.exceptions import RateLimited

    async def scenario() -> list[bool]:
        results: list[bool] = []
        for _ in range(times):
            try:
                await dependency(_request(), actor)
            except RateLimited:
                results.append(False)
            else:
                results.append(True)
        return results

    return asyncio.run(scenario())


def _actor() -> Any:
    from core.context import CurrentUser

    return CurrentUser(id="u-budget", name="x", roles=(), permissions=frozenset())


def test_override_replaces_the_hardcoded_limit(
    budget_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文案里写的 100 只是基线：运维说 1，就是 1。"""
    import api.deps as deps

    budget_env(rate_limit_overrides='{"demo.override": 1}')
    limiter = _limiter()
    monkeypatch.setattr(deps, "get_rate_limiter", lambda: limiter)

    dependency = rate_limit("demo.override", limit=100)
    assert _spam(dependency, _actor(), 3) == [True, False, False]


def test_scale_multiplies_every_scope(budget_env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """整体收紧一半：4 次额度变 2 次。"""
    import api.deps as deps

    budget_env(rate_limit_scale=0.5)
    limiter = _limiter()
    monkeypatch.setattr(deps, "get_rate_limiter", lambda: limiter)

    dependency = rate_limit("demo.scale", limit=4)
    assert _spam(dependency, _actor(), 4) == [True, True, False, False]


def test_scale_zero_turns_everything_off(
    budget_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`SCALE=0` 是运维的「全关」开关，必须真的全放行。"""
    import api.deps as deps

    budget_env(rate_limit_scale=0.0)
    limiter = _limiter()
    monkeypatch.setattr(deps, "get_rate_limiter", lambda: limiter)

    dependency = rate_limit("demo.off.scale", limit=1)
    assert _spam(dependency, _actor(), 5) == [True] * 5


def test_override_cannot_survive_scale_zero(
    budget_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """先覆盖后缩放：被单独放宽过的接口也不能从「全关」里漏出来。"""
    import api.deps as deps

    budget_env(rate_limit_overrides='{"demo.override.zero": 500}', rate_limit_scale=0.0)
    limiter = _limiter()
    monkeypatch.setattr(deps, "get_rate_limiter", lambda: limiter)

    dependency = rate_limit("demo.override.zero", limit=1)
    assert _spam(dependency, _actor(), 5) == [True] * 5


@pytest.mark.parametrize(
    ("raw", "hint"),
    [
        ("{not json", "合法 JSON"),
        ('{"a.b": true}', "非负整数"),
        ('{"a.b": -1}', "非负整数"),
        ('{"a.b": "10"}', "非负整数"),
        ('["a.b"]', "形式的对象"),
        ('{"a:b": 10}', "scope 非法"),
    ],
)
def test_bad_overrides_fail_loudly(budget_env: Any, raw: str, hint: str) -> None:
    """配置写错要炸在启动期。静默忽略一条额度配置，等于让人以为它生效了。"""
    from core.ratelimit import parsed_overrides

    budget_env(rate_limit_overrides=raw)
    with pytest.raises(ValueError, match=hint):
        parsed_overrides()


def test_every_override_names_a_real_scope(
    client: Any, budget_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配置里写了代码里不存在的 scope → 红。

    和豁免表同一个道理：手写清单不对账，就只是一份看起来很认真的错误信息。
    取 `client` 夹具是为了确保路由模块已被导入（`declared_scopes` 在 import 期填充）。
    """
    from core.ratelimit import declared_scopes, unknown_overrides

    known = declared_scopes()
    assert known, "路由没被导入：对账会永远通过（这正是它最该避免的失效方式）"

    budget_env(rate_limit_overrides='{"nope.not.real": 5}')
    assert unknown_overrides(known) == ["nope.not.real"]


def test_declared_scopes_includes_wired_route() -> None:
    """登记表至少包含真的挂上去的那个 scope——空表通过不算通过。

    自己 import 路由模块，不依赖别的用例先跑过（否则单跑这一个会假绿）。
    """
    from core.ratelimit import declared_scopes
    from modules.task.router import router  # noqa: F401 - 导入即登记 scope

    assert "task.create" in declared_scopes()


def test_scope_with_colon_fails_at_import_time() -> None:
    """笔误要在声明时就炸，不要拖到第一个请求。"""
    with pytest.raises(ValueError, match="冒号"):
        rate_limit("demo:bad", limit=1)
