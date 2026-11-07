"""运维端点：`/metrics`。

与业务路由**分开挂载**、**不进 OpenAPI**，原因有两个：

1. 它不该走业务鉴权：抓取方是 Prometheus，不是登录用户，没有 JWT 也不该有。
   凭据是独立的 `METRICS_TOKEN`（`Authorization: Bearer <token>`）。
2. 它不该出现在前端契约里：`api/docs/openapi.json` 是四份生成物之一，
   加一个运维端点进去会让前端的类型同步多一个无用的噪声项。

## 为什么生产环境没配 token 时返回 403 而不是放行

`/metrics` 会暴露路由模板、队列积压、驱动名、部署环境——对内部人员是信息，
对外部扫描器是地图。默认放行等于「谁先发现谁就能读」。生产环境请设置
`METRICS_TOKEN`（`app doctor` 会检查这一条）。

## token 只从请求头取

`?token=` 会进入访问日志、反向代理日志与浏览器历史；抓取配置里加一个
`authorization` 头就能正确工作，没有便利性上的理由牺牲它。
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from api.instrumentation import refresh_runtime_gauges
from core.config import settings
from core.metrics import METRICS

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _authorized(request: Request) -> bool:
    expected = settings.metrics_token
    if not expected:
        # 非生产环境允许匿名抓取：本地起 Prometheus 不用先配凭据
        return settings.app_env != "prod"
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return False
    return secrets.compare_digest(header[7:].strip(), expected)


def build_ops_router() -> APIRouter:
    router = APIRouter(tags=["运维"], include_in_schema=False)

    @router.get("/metrics", include_in_schema=False, summary="Prometheus 指标")
    async def metrics(request: Request) -> PlainTextResponse:
        if not settings.metrics_enabled:
            # 关掉时返回 404 而不是 403：不暴露「这里本来有个端点」
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
        if not _authorized(request):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="指标端点需要 Authorization: Bearer <METRICS_TOKEN>",
            )
        await refresh_runtime_gauges()
        return PlainTextResponse(METRICS.render(), media_type=CONTENT_TYPE)

    return router


__all__ = ["build_ops_router"]
