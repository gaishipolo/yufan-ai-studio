"""健康检查：存活 / 就绪探针。

两个探针回答的是不同问题，不能合并：

- `/health`：**进程还活着吗**。只要事件循环能回包就算 ok，不碰任何依赖，
  用于「要不要重启这个容器」。
- `/health/ready`：**现在能正确服务请求吗**。依赖不可用时必须暴露出来，
  否则编排系统会把一个数据库断连的实例继续留在负载均衡里。

就绪判定的实现在 `api/health.py`（装配层），本模块只做协议转换。
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi import status as http_status

from api.deps import SessionDep
from core.config import settings
from core.response import ApiResponse
from core.schema import CamelModel
from modules.health.checks import collect_runtime_status

router = APIRouter(prefix="/health", tags=["系统"])


class HealthOut(CamelModel):
    status: str = "ok"
    app: str
    env: str
    version: str = "0.1.0"


class RuntimeCheckOut(CamelModel):
    name: str
    ok: bool
    detail: str
    required: bool = True


class ReadyOut(CamelModel):
    """就绪状态。`checks` 说明每一项依赖的结论，status ∈ ok / degraded / down。"""

    status: str
    database: str
    queue_size: int
    checks: list[RuntimeCheckOut] = []


@router.get("", summary="存活探针")
async def health() -> ApiResponse[HealthOut]:
    return ApiResponse.ok(HealthOut(app=settings.app_name, env=settings.app_env))


@router.get(
    "/ready",
    summary="就绪探针（检查依赖）",
    responses={
        http_status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ApiResponse[ReadyOut],
            "description": "有必需依赖不可用（status=down），编排系统应摘掉本实例",
        }
    },
)
async def ready(session: SessionDep, response: Response) -> ApiResponse[ReadyOut]:
    """`status=down` 时返回 **503**，不是 200。

    编排系统只看状态码：返回 200 时，无论响应体里写的是 `degraded` 还是 `down`，
    k8s 都会把这台实例继续留在 Service 后面——判定了却没人执行，等于没判。
    这是探针和普通接口的关键区别：普通接口用响应体表达业务结果，探针必须用状态码。

    `degraded` 仍然返回 200：outbox 积压、缓存不可用这些「能服务但在漏」的情况，
    摘掉实例只会让剩下的实例扛更多，反而更容易雪崩。它该触发的是告警，不是摘除。
    """
    runtime = await collect_runtime_status(session)
    if runtime.down:
        response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
    return ApiResponse.ok(
        ReadyOut(
            status=runtime.status,
            database=runtime.database,
            queue_size=runtime.queue_size,
            checks=[
                RuntimeCheckOut(
                    name=check.name,
                    ok=check.ok,
                    detail=check.detail,
                    required=check.required,
                )
                for check in runtime.checks
            ],
        )
    )
