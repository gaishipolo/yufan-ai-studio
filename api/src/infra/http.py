"""出站 HTTP 客户端。

## 为什么要有这一层

在这之前，「调第三方」是各模块自己 `async with httpx.AsyncClient(...)`：
`modules/model_driver/libtv/executor.py`、`modules/image/pipeline.py`、
`generation/runner.py` 各建各的。
后果不是不够优雅，是**三件事对不齐**：

- 超时：一个 60 秒、一个 30 秒、一个没设（httpx 默认 5 秒连接 + 无限读）；
- 连接池：每次请求新建 client，keep-alive 完全用不上，高并发时 TIME_WAIT 堆积；
- 安全：只有成图下载做了 SSRF 校验，其它地方直接 `client.get(供应商给的地址)`。

所以这一层只做传输与纪律，不做业务：拿连接、设超时、跟重定向、限大小、有限重试。

## 两条硬规则

1. **重定向必须手动跟**。`follow_redirects=True` 会让 httpx 在内部跳转，
   而安全校验只能看到第一跳的 URL——第二跳就能被跳到 `169.254.169.254`。
   这里每一跳都重新过一遍 `guard`。
2. **下载必须有大小上限**。供应商返回 300MB 还是 3KB 不由我们决定，
   流式读取 + 累加中断是唯一不会把进程读死的写法。

## 不做什么

- 不做重试策略的业务判断：默认 `retries=0`。POST 重复提交等于重复计费，
  只有明确幂等的 GET/HEAD 才该由调用方传 `retries>0`。
- 不做熔断与限流：那是 `infra/limits`（按接口/租户）的职责，混在一起会让
  「一次供应商抖动」和「本机过载」看起来是同一个问题。
"""

from __future__ import annotations

import asyncio
import email.utils
import ipaddress
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from infra.driver_config import DriverSettings, driver_config

# 这些状态码重试有意义：限流、网关抖动、上游重启。
# 4xx 里除 408/425/429 之外都不重试——重试只会把「参数错了」拖成「等了 30 秒还是错」。
#
# **500 保留**。参考实现（PostgREST 客户端）把 500 排除了，理由是「查询失败重试
# 也一样」；但本仓库打的是生成类供应商与 CDN，「500」在这里的语义是网关抖动 /
# 上游重启（见上一行），正是最该重试的那类。照抄会把真实的上游故障变成
# 「一次就失败」。
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# 重定向上限。5 跳足够走完 CDN → 对象存储 → 签名地址，再多基本就是循环。
MAX_REDIRECTS = 5

#: 服务端要求「等太久」时不再重试的下限（秒）。
#:
#: 为什么不干脆睡过去：`Retry-After: 300` 意味着这个 worker 槽位要被占 5 分钟，
#: 而队列的租约/超时预算通常更短 —— 睡着等只会让「重试」变成「卡死」。超过这个
#: 值就把决定权交回上层（它可以选择换实例、稍后重投、或者直接报错给用户）。
MAX_RETRY_WAIT_SEC = 30.0

_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "metadata", "metadata.google.internal", "instance-data"}
)
_BLOCKED_SUFFIXES = (".localhost", ".internal", ".local", ".localdomain")


class HttpError(Exception):
    """出站请求失败（连接、超时、状态码、内容超限）。由中间件转 502。"""


@dataclass(frozen=True, slots=True)
class Fetched:
    """一次下载的结果。`final_url` 是重定向之后的地址，便于记进审计。"""

    data: bytes
    content_type: str
    final_url: str


@dataclass(frozen=True, slots=True)
class FetchedFile:
    """流式下载的结果：字节已经在盘上，进程内存没被它占过。"""

    path: Path
    content_type: str
    final_url: str
    size: int


class HttpSettings(DriverSettings):
    """出站请求的部署参数。"""

    model_config = driver_config("HTTP")

    timeout_sec: float = 30.0
    #: 单次下载的**总预算**（秒），覆盖重定向链与整个读取过程。
    #:
    #: `timeout_sec` 是「每次操作」的上限 —— 对端每 29 秒吐 1 字节就永远不触发
    #: 读超时。一个 300MB 的入库下载能这样挂住一个 worker 槽位好几天：队列不
    #: 再消费、请求不再完成，而日志里一条超时都没有。总预算给的是「整件事
    #: 最多花多久」，与单次超时是两个维度，两个都要有。
    total_timeout_sec: float = 300.0
    # 连接池上限。超过后请求会排队而不是报错，所以这个值应该 ≥ worker 并发 × 2。
    max_connections: int = 100
    user_agent: str = "yufan-ai-studio"


@lru_cache
def get_http_settings() -> HttpSettings:
    return HttpSettings()


_HTTP: httpx.AsyncClient | None = None


def get_http() -> httpx.AsyncClient:
    """取进程级共享 client。

    构造本身不建连接（httpx 是惰性连接），所以这里可以是同步函数；
    真正的连接建立发生在第一次 `await client.get(...)`。`close_http()` 之后
    再调用会重新建一个，行为与首次调用一致。
    """
    global _HTTP
    if _HTTP is not None and not _HTTP.is_closed:
        return _HTTP
    config = get_http_settings()
    _HTTP = httpx.AsyncClient(
        timeout=httpx.Timeout(config.timeout_sec),
        limits=httpx.Limits(
            max_connections=max(1, config.max_connections),
            max_keepalive_connections=max(1, config.max_connections // 2),
        ),
        headers={"User-Agent": config.user_agent},
        # 硬规则 1：自己跟重定向，每一跳都要过安全校验
        follow_redirects=False,
    )
    return _HTTP


async def close_http() -> None:
    """关闭共享 client。在 lifespan 的 finally 里调用。"""
    global _HTTP
    client, _HTTP = _HTTP, None
    if client is not None and not client.is_closed:
        await client.aclose()


def reset_http_client() -> None:
    """仅测试使用：丢弃引用，不负责关闭（测试里的 client 通常没建立连接）。"""
    global _HTTP
    _HTTP = None


def validate_public_url(url: str, *, allowed_hosts: frozenset[str] = frozenset()) -> None:
    """下载前校验 URL，不合法抛 `ValueError`。

    URL 常常来自第三方响应体，而供应商地址本身是运维可配的环境变量——
    配置指错、上游被劫持、响应被篡改都能把它变成 SSRF 入口。

    配了 `allowed_hosts` 就只认白名单（生产必须配）；没配时退回黑名单兜底，
    只拒绝非公网 IP 字面量与明显的内网域名。**兜底不解析 DNS**，
    域名解析到内网时拦不住，所以它只能防「手滑」，防不了「被攻击」。
    """
    parts = urlparse(url)
    if parts.scheme not in ("https", "http"):
        raise ValueError(f"地址协议不可用：{parts.scheme!r}")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("地址缺少 host")

    if allowed_hosts:
        if host not in allowed_hosts:
            raise ValueError(f"host 不在白名单里：{host}")
        return

    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        addr = None
    if addr is not None and (
        addr.is_private or addr.is_loopback or addr.is_link_local or not addr.is_global
    ):
        raise ValueError(f"地址指向非公网 IP：{host}")
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise ValueError(f"地址指向内网域名：{host}")


async def fetch_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout: float | None = None,  # noqa: ASYNC109 —— 传给 httpx 的单次超时，非总预算
    retries: int = 0,
    guard: Callable[[str], None] = validate_public_url,
) -> Fetched:
    """下载到内存，带大小上限与逐跳校验。

    只用于「产物下载」这类小文件（图片、封面）。视频原片请走流式落盘，
    几十上百 MB 全读进内存的峰值是「并发数 × 文件大小」。
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes 必须为正数")

    last_error: str = ""
    for attempt in range(retries + 1):
        try:
            return await _fetch_once(url, max_bytes=max_bytes, timeout=timeout, guard=guard)
        except _Retryable as exc:
            last_error = str(exc)
            if attempt >= retries:
                break
            wait = _backoff_delay(attempt, server_hint=exc.retry_after)
            if wait > MAX_RETRY_WAIT_SEC:
                # 服务端要求等太久：不占着 worker 槽位干等，把决定权交回上层
                raise HttpError(
                    f"下载失败（服务端要求 {wait:.0f} 秒后重试）：{last_error}"
                ) from exc
            await asyncio.sleep(wait)
    raise HttpError(f"下载失败（已重试 {retries} 次）：{last_error}")


class _Retryable(Exception):
    """内部信号：这一次失败值得重试。不对外暴露，避免调用方依赖具体状态码。

    `retry_after` 是服务端在 `Retry-After` 头里给出的秒数（如果有）。它比本地
    退避序列更权威 —— 对方知道自己的恢复节奏，我们只能猜。
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """解析 `Retry-After`（秒数或 HTTP-date 两种格式），解析不出来返回 None。"""
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    # 格式一：秒数（429/503 最常见）
    if text.isdigit():
        return float(text)
    # 格式二：HTTP-date（RFC 7231）
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(tz=UTC)).total_seconds())


def _backoff_delay(attempt: int, *, server_hint: float | None = None) -> float:
    """下一次重试前等多久。

    ## 为什么加抖动（这条是刻意改掉原来决定的）

    原来的注释写「不引入随机抖动：同批次任务本来就错峰投递，再加抖动只会让
    排查时的时间线更难对齐」。那个判断低估了一件事：**固定序列会让同批失败者
    在同一刻一起重试**。供应商抖动时一批任务同时失败是常态（同一个上游、同一
    个窗口），固定退避把它们对齐成一次新的尖峰，刚缓过来的服务端被再打一遍。

    代价是日志时间线更难对齐 —— 这是排查的成本，惊群是可用性的成本，这里选
    后者。抖动取「**0.5~1.0 倍**」而不是「0~1 倍」：保留指数增长的下限，
    避免多个任务在几乎同一时刻重试。

    ## 服务端给了 Retry-After 就用它

    对方知道自己的恢复节奏，本地退避只是猜。给了就照做（上限由调用方判：
    `MAX_RETRY_WAIT_SEC` 以上不重试）。
    """
    if server_hint is not None:
        return server_hint
    base = min(8.0, 0.5 * (2**attempt))
    return base * (0.5 + 0.5 * random.random())


async def _fetch_once(
    url: str,
    *,
    max_bytes: int,
    timeout: float | None,  # noqa: ASYNC109 —— 同一预算透传给 httpx
    guard: Callable[[str], None],
) -> Fetched:
    """一次下载尝试。外层套**总预算**，内层是逐跳下载。"""
    budget = get_http_settings().total_timeout_sec
    try:
        async with asyncio.timeout(budget):
            return await _download_bytes(
                url, max_bytes=max_bytes, timeout=timeout, guard=guard
            )
    except TimeoutError as exc:
        # `asyncio.timeout` 抛的是内置 TimeoutError，与 httpx 的读超时不同类，
        # 所以这里不会吞掉内层已经转成 `_Retryable` 的那次超时。
        raise _Retryable(f"下载超过总预算（{budget:.0f} 秒）") from exc


async def _download_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout: float | None,  # noqa: ASYNC109 —— 同一预算透传给 httpx
    guard: Callable[[str], None],
) -> Fetched:
    client = get_http()
    current = url

    for _hop in range(MAX_REDIRECTS + 1):
        guard(current)
        try:
            async with client.stream("GET", current, timeout=timeout) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise HttpError(f"重定向缺少 Location：{current}")
                    # 相对地址要基于当前跳解析，否则 /a → b.png 会拼错
                    current = urljoin(current, location)
                    continue

                if response.status_code in RETRY_STATUS:
                    raise _Retryable(
                        f"{current} 返回 {response.status_code}",
                        retry_after=_retry_after_seconds(response),
                    )
                if response.status_code >= 400:
                    raise HttpError(f"{current} 返回 {response.status_code}")

                declared = response.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                    raise HttpError(
                        f"内容超过上限：{declared} > {max_bytes} 字节（{current}）"
                    )

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise HttpError(f"内容超过上限：已读 {total} > {max_bytes} 字节")
                    chunks.append(chunk)
                return Fetched(
                    data=b"".join(chunks),
                    content_type=response.headers.get("content-type", ""),
                    final_url=str(response.url),
                )
        except httpx.TimeoutException as exc:
            raise _Retryable(f"超时：{exc}") from exc
        except httpx.TransportError as exc:
            raise _Retryable(f"传输错误：{exc}") from exc

    raise HttpError(f"重定向超过 {MAX_REDIRECTS} 次：{url}")


async def fetch_to_file(
    url: str,
    target: Path,
    *,
    max_bytes: int,
    timeout: float | None = None,  # noqa: ASYNC109 —— 传给 httpx 的单次超时，非总预算
    retries: int = 0,
    guard: Callable[[str], None] = validate_public_url,
) -> FetchedFile:
    """下载到**文件**，边读边写，进程内存与文件大小无关。

    `fetch_bytes` 适合小文件（图片、封面）；入库那条链允许 300MB 的成片，
    整份读进内存的峰值是「并发数 × 文件大小」——两个并发就能把进程打挂，
    而且小文件完全测不出来。这条路径与它共用同一套纪律：逐跳校验、大小上限、
    有限重试、手动跟重定向；区别只在写盘。

    失败时把写了一半的文件删掉：留着会被下游当成「已就绪」的产物。
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes 必须为正数")

    last_error = ""
    for attempt in range(retries + 1):
        try:
            return await _fetch_once_to_file(
                url, target, max_bytes=max_bytes, timeout=timeout, guard=guard
            )
        except _Retryable as exc:
            last_error = str(exc)
            if attempt >= retries:
                break
            wait = _backoff_delay(attempt, server_hint=exc.retry_after)
            if wait > MAX_RETRY_WAIT_SEC:
                # 服务端要求等太久：不占着 worker 槽位干等，把决定权交回上层
                raise HttpError(
                    f"下载失败（服务端要求 {wait:.0f} 秒后重试）：{last_error}"
                ) from exc
            await asyncio.sleep(wait)
    raise HttpError(f"下载失败（已重试 {retries} 次）：{last_error}")


async def _fetch_once_to_file(
    url: str,
    target: Path,
    *,
    max_bytes: int,
    timeout: float | None,  # noqa: ASYNC109 —— 同一预算透传给 httpx
    guard: Callable[[str], None],
) -> FetchedFile:
    """一次落盘下载尝试。外层套**总预算**，内层是逐跳下载。"""
    budget = get_http_settings().total_timeout_sec
    try:
        async with asyncio.timeout(budget):
            return await _download_to_file(
                url, target, max_bytes=max_bytes, timeout=timeout, guard=guard
            )
    except TimeoutError as exc:
        raise _Retryable(f"下载超过总预算（{budget:.0f} 秒）") from exc


async def _download_to_file(
    url: str,
    target: Path,
    *,
    max_bytes: int,
    timeout: float | None,  # noqa: ASYNC109 —— 同一预算透传给 httpx
    guard: Callable[[str], None],
) -> FetchedFile:
    client = get_http()
    current = url

    for _hop in range(MAX_REDIRECTS + 1):
        guard(current)
        try:
            async with client.stream("GET", current, timeout=timeout) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise HttpError(f"重定向缺少 Location：{current}")
                    current = urljoin(current, location)
                    continue

                if response.status_code in RETRY_STATUS:
                    raise _Retryable(
                        f"{current} 返回 {response.status_code}",
                        retry_after=_retry_after_seconds(response),
                    )
                if response.status_code >= 400:
                    raise HttpError(f"{current} 返回 {response.status_code}")

                declared = response.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                    raise HttpError(
                        f"内容超过上限：{declared} > {max_bytes} 字节（{current}）"
                    )

                handle = await asyncio.to_thread(target.open, "wb")
                total = 0
                try:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise HttpError(
                                f"内容超过上限：已读 {total} > {max_bytes} 字节"
                            )
                        # 写盘是阻塞的：大文件时一块 write 就能把同 worker 的
                        # 请求一起卡住，丢线程池。
                        await asyncio.to_thread(handle.write, chunk)
                except BaseException:
                    await asyncio.to_thread(handle.close)
                    await asyncio.to_thread(target.unlink, True)
                    raise
                else:
                    await asyncio.to_thread(handle.close)
                return FetchedFile(
                    path=target,
                    content_type=response.headers.get("content-type", ""),
                    final_url=str(response.url),
                    size=total,
                )
        except httpx.TimeoutException as exc:
            raise _Retryable(f"超时：{exc}") from exc
        except httpx.TransportError as exc:
            raise _Retryable(f"传输错误：{exc}") from exc

    raise HttpError(f"重定向超过 {MAX_REDIRECTS} 次：{url}")


__all__ = [
    "Fetched",
    "FetchedFile",
    "HttpError",
    "HttpSettings",
    "RETRY_STATUS",
    "close_http",
    "fetch_bytes",
    "fetch_to_file",
    "get_http",
    "get_http_settings",
    "reset_http_client",
    "validate_public_url",
]
