"""镜头词条路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.deps import SessionDep, require
from core.context import CurrentUser
from core.response import ApiResponse
from modules.shot.schema import ShotListOut
from modules.shot.service import ShotService

router = APIRouter(prefix="/shots", tags=["镜头库"])


def get_shot_service(session: SessionDep) -> ShotService:
    return ShotService(session)


ShotServiceDep = Annotated[ShotService, Depends(get_shot_service)]

# ---------- 权限依赖 ----------
ShotReadDep = Annotated[CurrentUser, Depends(require("shot:read"))]


@router.get("", summary="镜头词条列表")
async def list_shots(
    service: ShotServiceDep,
    actor: ShotReadDep,
    keyword: Annotated[str, Query(max_length=100)] = "",
) -> ApiResponse[ShotListOut]:
    return ApiResponse.ok(await service.list_shots(keyword=keyword))
