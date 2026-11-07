"""HTTP 中间件。

`RequestContextMiddleware` 是纯 ASGI 实现（非 BaseHTTPMiddleware），
保证 contextvars 能可靠地向下游传递，并给每个响应打上 `X-Request-Id`。
"""

from __future__ import annotations

import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.instrumentation import IN_FLIGHT, observe_request, route_template
from core.context import set_request_id
from core.logging import get_logger

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
