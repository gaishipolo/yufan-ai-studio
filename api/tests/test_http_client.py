"""出站 HTTP 客户端的行为测试。

三条最容易被实现写错的：重定向的每一跳都要过安全校验、内容超限要当场中断、
重试不能把 4xx 也一起重试。用 `MockTransport` 打真实请求路径，不 mock 内部函数。
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest

from infra import http as http_module
from infra.http import HttpError, fetch_bytes, validate_public_url


def _install(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    monkeypatch.setattr(http_module, "_HTTP", client)


# --------------------------------------------------------------------------- #
# URL 校验
# --------------------------------------------------------------------------- #
def test_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="协议"):
        validate_public_url("file:///etc/passwd")


def test_rejects_private_ip_literal() -> None:
    with pytest.raises(ValueError, match="非公网"):
        validate_public_url("http://10.0.0.5/x.png")


def test_rejects_link_local_metadata() -> None:
    with pytest.raises(ValueError, match="非公网"):
        validate_public_url("http://169.254.169.254/latest/meta-data/")


def test_rejects_internal_domain() -> None:
    with pytest.raises(ValueError, match="内网域名"):
        validate_public_url("http://localhost:8080/x")


def test_allows_public_host() -> None:
    validate_public_url("https://cdn.example.com/a.png")  # 不抛即通过


def test_allowlist_only_accepts_listed_host() -> None:
    allowed = frozenset({"cdn.example.com"})
    validate_public_url("https://cdn.example.com/a.png", allowed_hosts=allowed)
    with pytest.raises(ValueError, match="白名单"):
        validate_public_url("https://evil.example.net/a.png", allowed_hosts=allowed)


# --------------------------------------------------------------------------- #
# 客户端生命周期
# --------------------------------------------------------------------------- #
def test_get_http_reuses_one_client() -> None:
    http_module.reset_http_client()
    try:
        first = http_module.get_http()
        assert http_module.get_http() is first
    finally:
        asyncio.run(http_module.close_http())


def test_close_allows_reopen() -> None:
    http_module.reset_http_client()
    first = http_module.get_http()
    asyncio.run(http_module.close_http())
    assert first.is_closed
    second = http_module.get_http()
    assert second is not first
    asyncio.run(http_module.close_http())


# --------------------------------------------------------------------------- #
# 下载
# --------------------------------------------------------------------------- #
def test_fetch_returns_bytes_and_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        lambda _req: httpx.Response(
            200, content=b"png", headers={"content-type": "image/png"}
        ),
    )
    result = asyncio.run(fetch_bytes("https://cdn.example.com/a.png", max_bytes=1024))
    assert result.data == b"png"
    assert result.content_type == "image/png"
    assert result.final_url.endswith("/a.png")


def test_redirect_target_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    """第一跳公网、第二跳内网——必须在第二跳被拦住，而不是跟着跳过去。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    _install(monkeypatch, handler)
    with pytest.raises(ValueError, match="非公网"):
        asyncio.run(fetch_bytes("https://cdn.example.com/a.png", max_bytes=1024))


def test_relative_redirect_is_joined(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a.png":
            return httpx.Response(302, headers={"location": "/real.png"})
        return httpx.Response(200, content=b"ok")

    _install(monkeypatch, handler)
    result = asyncio.run(fetch_bytes("https://cdn.example.com/a.png", max_bytes=1024))
    assert result.data == b"ok"
    assert result.final_url.endswith("/real.png")


def test_declared_size_over_limit_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda _req: httpx.Response(200, content=b"x" * 100))
    with pytest.raises(HttpError, match="超过上限"):
        asyncio.run(fetch_bytes("https://cdn.example.com/a.png", max_bytes=10))


def test_client_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    _install(monkeypatch, handler)
    with pytest.raises(HttpError, match="404"):
        asyncio.run(fetch_bytes("https://cdn.example.com/a.png", max_bytes=1024, retries=2))
    # 4xx 重试只会把「参数错了」拖成「等了 30 秒还是错」
    assert calls["n"] == 1


def test_retryable_status_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=b"ok")

    async def _no_sleep(_seconds: float) -> None:
        return None

    _install(monkeypatch, handler)
    monkeypatch.setattr(http_module.asyncio, "sleep", _no_sleep)
    result = asyncio.run(fetch_bytes("https://cdn.example.com/a.png", max_bytes=1024, retries=1))
    assert result.data == b"ok"
    assert calls["n"] == 2


def test_too_many_redirects_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": str(request.url)})

    _install(monkeypatch, handler)
    with pytest.raises(HttpError, match="重定向"):
        asyncio.run(fetch_bytes("https://cdn.example.com/a.png", max_bytes=1024))


def test_zero_max_bytes_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_bytes"):
        asyncio.run(fetch_bytes("https://cdn.example.com/a.png", max_bytes=0))
