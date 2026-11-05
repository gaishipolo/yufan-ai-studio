"""健康检查：存活 / 就绪探针。"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from api.deps import SessionDep
from core.config import settings
from core.response import ApiResponse
from core.schema import CamelModel
from infra import get_queue

router = APIRouter(prefix="/health", tags=["系统"])


class HealthOut(CamelModel):
    status: str = "ok"
    app: str
    env: str
    version: str = "0.1.0"


class ReadyOut(CamelModel):
    status: str
    database: str
    queue_size: int


@router.get("", summary="存活探针")
async def health() -> ApiResponse[HealthOut]:
    return ApiResponse.ok(HealthOut(app=settings.app_name, env=settings.app_env))


@router.get("/ready", summary="就绪探针（检查依赖）")
async def ready(session: SessionDep) -> ApiResponse[ReadyOut]:
    db_status = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001  探针不应抛出
        db_status = "down"

    try:
        queue_size = await get_queue().size()
    except Exception:  # noqa: BLE001
        queue_size = -1

    overall = "ok" if db_status == "ok" else "degraded"
    return ApiResponse.ok(ReadyOut(status=overall, database=db_status, queue_size=queue_size))
