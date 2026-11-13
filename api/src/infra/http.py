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
import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urljoin, urlparse

import httpx

from infra.driver_config import DriverSettings, driver_config

# 这些状态码重试有意义：限流、网关抖动、上游重启。
# 4xx 里除 408/425/429 之外都不重试——重试只会把「参数错了」拖成「等了 30 秒还是错」。
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# 重定向上限。5 跳足够走完 CDN → 对象存储 → 签名地址，再多基本就是循环。
MAX_REDIRECTS = 5

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


class HttpSettings(DriverSettings):
    """出站请求的部署参数。"""

    model_config = driver_config("HTTP")

    timeout_sec: float = 30.0
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
            # 0.5s、1s、2s…上限 8s。不引入随机抖动：同批次任务本来就错峰投递，
            # 再加抖动只会让排查时的时间线更难对齐。
            await asyncio.sleep(min(8.0, 0.5 * (2**attempt)))
    raise HttpError(f"下载失败（已重试 {retries} 次）：{last_error}")


class _Retryable(Exception):
    """内部信号：这一次失败值得重试。不对外暴露，避免调用方依赖具体状态码。"""


async def _fetch_once(
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
                    raise _Retryable(f"{current} 返回 {response.status_code}")
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


__all__ = [
    "Fetched",
    "HttpError",
    "HttpSettings",
    "RETRY_STATUS",
    "close_http",
    "fetch_bytes",
    "get_http",
    "get_http_settings",
    "reset_http_client",
    "validate_public_url",
]
