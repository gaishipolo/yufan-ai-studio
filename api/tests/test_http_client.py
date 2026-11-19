"""出站 HTTP 客户端的行为测试。

三条最容易被实现写错的：重定向的每一跳都要过安全校验、内容超限要当场中断、
重试不能把 4xx 也一起重试。用 `MockTransport` 打真实请求路径，不 mock 内部函数。
"""
from __future__ import annotations

import asyncio
import email.utils
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

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


# --------------------------------------------------------------------------- #
# 流式下载（`fetch_to_file`）：入库那条链允许 300MB，不能整份进内存
# --------------------------------------------------------------------------- #
class _AsyncChunks(httpx.AsyncByteStream):
    """分块响应体。`MockTransport` + `AsyncClient` 只认异步流，传 `iter(list)` 会报错。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):  # type: ignore[override]
        for chunk in self._chunks:
            yield chunk


def _chunked(chunks: list[bytes]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, stream=_AsyncChunks(chunks), headers={"content-type": "video/mp4"}
        )

    return handler


def test_fetch_to_file_writes_the_bytes(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from infra.http import fetch_to_file

    target = tmp_path / "out.mp4"
    _install(monkeypatch, _chunked([b"a" * 1024, b"b" * 512]))

    fetched = asyncio.run(
        fetch_to_file("https://cdn.example/v.mp4", target, max_bytes=10_000)
    )

    assert fetched.size == 1536
    assert target.read_bytes() == b"a" * 1024 + b"b" * 512
    assert fetched.content_type == "video/mp4"


def test_fetch_to_file_rejects_declared_oversize(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infra.http import HttpError, fetch_to_file

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 10, headers={"content-length": "999999"})

    _install(monkeypatch, handler)
    target = tmp_path / "big.mp4"

    with pytest.raises(HttpError, match="超过上限"):
        asyncio.run(fetch_to_file("https://cdn.example/big.mp4", target, max_bytes=1024))
    assert not target.exists()


def test_fetch_to_file_deletes_partial_file_when_stream_overflows(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有 content-length 时只能边读边数：超了要立刻中断，并删掉写了一半的文件。"""
    from infra.http import HttpError, fetch_to_file

    _install(monkeypatch, _chunked([b"x" * 600, b"y" * 600]))
    target = tmp_path / "partial.mp4"

    with pytest.raises(HttpError, match="超过上限"):
        asyncio.run(fetch_to_file("https://cdn.example/p.mp4", target, max_bytes=1000))
    assert not target.exists(), "半个文件留着会被下游当成已就绪的产物"


def test_a_slow_drip_download_hits_the_total_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """慢速滴流的响应必须被**总预算**打断。

    httpx 的 `timeout` 是「每次操作」的上限：对端每 29 秒吐 1 字节，就永远
    不触发读超时。一个 300MB 的入库下载能这样挂住一个 worker 槽位好几天 ——
    队列不再消费、请求不再完成，而日志里一条超时都没有。

    「每次读有超时」防不住这种对端；「整件事有预算」才防得住。
    """

    async def _drip() -> Any:
        # 每 50 毫秒 1 字节：远小于任何单次读超时，但总量可以无限拖下去
        for _ in range(1000):
            await asyncio.sleep(0.05)
            yield b"x"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_drip())

    _install(monkeypatch, handler)
    monkeypatch.setattr(
        http_module,
        "get_http_settings",
        lambda: http_module.HttpSettings(total_timeout_sec=0.3),
    )

    with pytest.raises(HttpError, match="总预算"):
        asyncio.run(fetch_bytes("http://example.com/slow", max_bytes=10**6))




# --------------------------------------------------------------------------- #
# 重试策略：抖动与 Retry-After
# --------------------------------------------------------------------------- #
def test_backoff_has_jitter() -> None:
    """同一 attempt 多次取值不能全等 —— 固定序列会让同批失败者同时重试。

    供应商抖动时一批任务同时失败是常态，固定退避把它们对齐成一次新的尖峰。
    代价是日志时间线更难对齐（原来正是为此不加抖动），但惊群是可用性成本。
    """
    from infra.http import _backoff_delay

    samples = {round(_backoff_delay(3), 6) for _ in range(8)}
    assert len(samples) > 1, "退避完全没有抖动"
    # 抖动范围是 0.5~1.0 倍，且不超过上限 8 秒
    assert all(0.5 * 4.0 <= value <= 8.0 for value in samples)


def test_backoff_prefers_the_server_hint() -> None:
    from infra.http import _backoff_delay

    assert _backoff_delay(0, server_hint=7.5) == 7.5
    assert _backoff_delay(9, server_hint=0.0) == 0.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3", 3.0),
        ("0", 0.0),
        ("", None),
        ("not-a-date", None),
    ],
)
def test_retry_after_parsing(raw: str, expected: float | None) -> None:
    from infra.http import _retry_after_seconds

    response = httpx.Response(429, headers={"retry-after": raw} if raw else {})
    assert _retry_after_seconds(response) == expected


def test_retry_after_http_date_is_converted_to_seconds() -> None:
    from infra.http import _retry_after_seconds

    future = email.utils.format_datetime(
        datetime.now(tz=UTC) + timedelta(seconds=5), usegmt=True
    )
    response = httpx.Response(503, headers={"retry-after": future})
    seconds = _retry_after_seconds(response)
    assert seconds is not None and 3.0 <= seconds <= 6.0


def test_server_asking_to_wait_too_long_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Retry-After` 超过上限时不睡过去，直接交给上层。

    睡着等 5 分钟会把 worker 槽位占死（队列租约通常更短），「重试」变成「卡死」。
    """
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "600"})

    _install(monkeypatch, handler)

    with pytest.raises(HttpError, match="服务端要求"):
        asyncio.run(fetch_bytes("http://example.com/x", max_bytes=1024, retries=3))
    assert calls["n"] == 1, "要求等太久时不该再发第二次"


def test_retry_after_is_used_as_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """服务端给了 Retry-After 就按它等，不走本地退避序列。"""
    sleeps: list[float] = []
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, headers={"retry-after": "2"})
        return httpx.Response(200, content=b"ok")

    _install(monkeypatch, handler)

    async def fake_sleep(value: float) -> None:
        sleeps.append(value)

    monkeypatch.setattr(http_module.asyncio, "sleep", fake_sleep)

    result = asyncio.run(fetch_bytes("http://example.com/x", max_bytes=1024, retries=1))
    assert result.data == b"ok"
    assert sleeps == [2.0], f"应当按 Retry-After 等 2 秒，实际 {sleeps}"
