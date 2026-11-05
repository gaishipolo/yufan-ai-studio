"""HTTP 中间件。

`RequestContextMiddleware` 是纯 ASGI 实现（非 BaseHTTPMiddleware），
保证 contextvars 能可靠地向下游传递，并给每个响应打上 `X-Request-Id`。
"""

from __future__ import annotations

import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.context import set_request_id
from core.logging import get_logger

logger = get_logger("app.access")

REQUEST_ID_HEADER = "X-Request-Id"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        rid = headers.get(REQUEST_ID_HEADER.lower().encode(), b"").decode() or uuid.uuid4().hex[:16]
        set_request_id(rid)

        started = time.perf_counter()
        status_code = 500

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
            logger.info(
                "%s %s -> %d (%.1fms)",
                scope.get("method", "-"),
                scope.get("path", "-"),
                status_code,
                elapsed_ms,
            )
