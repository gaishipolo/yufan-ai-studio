"""HTTP 中间件。

`RequestContextMiddleware` 是纯 ASGI 实现（非 BaseHTTPMiddleware），
保证 contextvars 能可靠地向下游传递，并给每个响应打上 `X-Request-Id`。
"""

from __future__ import annotations

import json
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.instrumentation import IN_FLIGHT, observe_request, route_template
from core.context import set_request_id
from core.exceptions import ErrorCode, RateLimited
from core.logging import get_logger
from core.ratelimit import get_rate_limiter, retry_after_seconds

logger = get_logger("app.access")

REQUEST_ID_HEADER = "X-Request-Id"

# 只接受「看起来像 id」的值，其余一律丢掉自己生成。
#
# 这个头是**客户端可控**的，而且会被原样写进这个请求的每一条日志、并回写到响应头：
# - 不限长度：64 KB 的 id 会把日志行的单行上限打爆，这条请求的所有上下文一起丢；
# - 不限字符集：`\t`、控制字符、超长 Unicode 能让一条日志看起来像两条；
# - 可伪造关联：别人把你的 id 塞进自己的请求，两条无关链路就在日志里粘在一起了。
# 不合法就自己生成，**不报错**——这是辅助头，不该让请求失败（R8-03）。
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _resolve_request_id(raw: bytes) -> str:
    try:
        candidate = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return uuid.uuid4().hex[:16]
    return candidate if _REQUEST_ID_RE.fullmatch(candidate) else uuid.uuid4().hex[:16]


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        rid = _resolve_request_id(headers.get(REQUEST_ID_HEADER.lower().encode(), b""))
        set_request_id(rid)

        started = time.perf_counter()
        status_code = 500
        IN_FLIGHT.inc()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                resp_headers = MutableHeaders(scope=message)
                resp_headers[REQUEST_ID_HEADER] = rid
                resp_headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            IN_FLIGHT.dec()
            # 路由模板而不是真实路径：`/tasks/abc123` 每个 id 都会变成一条时间序列
            observe_request(
                method=scope.get("method", "-"),
                route=route_template(scope),
                status=status_code,
                elapsed=elapsed_ms / 1000,
            )
            logger.info(
                "%s %s -> %d (%.1fms)",
                scope.get("method", "-"),
                scope.get("path", "-"),
                status_code,
                elapsed_ms,
            )


# 不算「客户端在刷接口」的路径：探针被限流等于编排系统自己制造故障，
# 指标被限流等于出事时看不到现场，文档与静态资源是浏览器一次加载几十个请求。
DEFAULT_RATE_LIMIT_EXCLUDE = (
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/static",
    "/favicon.ico",
)


class GlobalRateLimitMiddleware:
    """全局兜底限流：按客户端 IP 计，**默认关闭**（`RATE_LIMIT_GLOBAL_PER_MIN=0`）。

    为什么需要它：路由级 `rate_limit()` 要求每个接口显式挂，漏挂的接口完全裸奔。
    为什么默认关：它按 IP 计，反代/NAT 后面一个出口 IP 代表很多真实用户，额度定小了
    就是「一栋楼一起 429」；而且 `scope["client"]` 在反代后面全是代理的地址，
    除非部署层已经正确透传真实 IP（`X-Forwarded-For` 是客户端可伪造的，
    中间件不能自己信），否则这个维度本身就是错的。所以它是**可选兜底**，
    不是默认防护——默认防护仍然应该挂在具体接口上。
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limit_per_min: int = 0,
        exclude: tuple[str, ...] = DEFAULT_RATE_LIMIT_EXCLUDE,
    ) -> None:
        self.app = app
        self.limit = limit_per_min
        self.exclude = exclude

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.limit <= 0 or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        if any(path.startswith(prefix) for prefix in self.exclude):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        subject = str(client[0]) if client else "unknown"
        decision = await get_rate_limiter().hit(
            subject, scope="global", limit=self.limit, window=60.0
        )
        if decision.allowed:
            await self.app(scope, receive, send)
            return

        logger.warning(
            "全局限流命中：ip=%s path=%s used=%d/%d", subject, path, decision.used, decision.limit
        )
        await _send_rate_limited(send, retry_after=retry_after_seconds(decision))


async def _send_rate_limited(send: Send, *, retry_after: int) -> None:
    """不经过 FastAPI 的异常处理器：中间件在路由之前，那时还没有路由上下文。"""
    body = json.dumps(
        {"code": int(ErrorCode.RATE_LIMITED), "message": RateLimited.message, "data": None},
        ensure_ascii=False,
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": RateLimited.http_status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
                (b"retry-after", str(retry_after).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
