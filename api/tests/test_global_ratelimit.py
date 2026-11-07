"""全局限流中间件：只在「该拦的时候拦、该放的时候放」上做断言。

这个中间件默认关闭，所以最容易被误用成「开了却没用」或「开了把探针打死」。
两类用例各占一半：额度生效、以及**绝不能限流**的路径。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from api import middleware as middleware_mod
from api.middleware import GlobalRateLimitMiddleware
from core.ratelimit import RateLimiter
from infra.cache.memory import MemoryCache


class _StubApp:
    """下游应用：只记录被调用次数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.calls += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def _call(
    mw: GlobalRateLimitMiddleware,
    *,
    path: str = "/api/v1/tasks",
    ip: str = "1.2.3.4",
) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "client": (ip, 12345),
    }
    await mw(scope, receive, send)
    return sent


def _status(sent: list[dict[str, Any]]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _headers(sent: list[dict[str, Any]]) -> dict[bytes, bytes]:
    return dict(next(m["headers"] for m in sent if m["type"] == "http.response.start"))


def _body(sent: list[dict[str, Any]]) -> dict[str, Any]:
    raw = next(m["body"] for m in sent if m["type"] == "http.response.body")
    return json.loads(raw)


@pytest.fixture()
def limiter(monkeypatch: pytest.MonkeyPatch) -> RateLimiter:
    """把中间件用的限流器换成独立实例，避免与业务接口共用计数键。"""
    instance = RateLimiter(MemoryCache(), prefix="test")
    monkeypatch.setattr(middleware_mod, "get_rate_limiter", lambda: instance)
    return instance


async def test_disabled_forwards_every_request(limiter: RateLimiter) -> None:
    """额度 0 = 关闭：中间件必须整层透传，而不是「限额为零、全拦」。"""
    app = _StubApp()
    mw = GlobalRateLimitMiddleware(app, limit_per_min=0, exclude=())

    assert [_status(await _call(mw)) for _ in range(5)] == [200] * 5
    assert app.calls == 5


async def test_over_limit_returns_429_with_retry_after(limiter: RateLimiter) -> None:
    app = _StubApp()
    mw = GlobalRateLimitMiddleware(app, limit_per_min=2, exclude=())

    assert _status(await _call(mw)) == 200
    assert _status(await _call(mw)) == 200
    sent = await _call(mw)

    assert _status(sent) == 429
    assert int(_headers(sent)[b"retry-after"]) >= 1
    assert _body(sent)["code"] == 42900
    assert app.calls == 2, "被拦的请求不该打到下游"


async def test_excluded_paths_never_limited(limiter: RateLimiter) -> None:
    """探针被限流 = 编排系统自己制造故障；指标被限流 = 出事时看不到现场。"""
    app = _StubApp()
    mw = GlobalRateLimitMiddleware(
        app, limit_per_min=1, exclude=("/health", "/metrics", "/api/v1/health")
    )

    for _ in range(5):
        assert _status(await _call(mw, path="/health")) == 200
        assert _status(await _call(mw, path="/metrics")) == 200
        assert _status(await _call(mw, path="/api/v1/health/ready")) == 200
    assert app.calls == 15


async def test_each_client_has_own_bucket(limiter: RateLimiter) -> None:
    app = _StubApp()
    mw = GlobalRateLimitMiddleware(app, limit_per_min=1, exclude=())

    assert _status(await _call(mw, ip="10.0.0.1")) == 200
    assert _status(await _call(mw, ip="10.0.0.1")) == 429
    # 另一个 IP 不该被邻居的用量牵连
    assert _status(await _call(mw, ip="10.0.0.2")) == 200


async def test_fails_open_when_counter_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    """限流器自己挂了不能变成全站 429——它是保护措施，不是鉴权。"""

    class _Broken:
        async def incr(self, key: str, delta: int = 1, *, ttl: float | None = None) -> int:
            raise RuntimeError("cache down")

    monkeypatch.setattr(
        middleware_mod, "get_rate_limiter", lambda: RateLimiter(_Broken(), prefix="test")
    )
    app = _StubApp()
    mw = GlobalRateLimitMiddleware(app, limit_per_min=1, exclude=())

    assert _status(await _call(mw)) == 200
    assert _status(await _call(mw)) == 200
