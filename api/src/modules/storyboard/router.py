"""分镜模板路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from api.deps import SessionDep, require
from core.context import CurrentUser
from core.response import ApiResponse
from modules.storyboard.schema import StoryboardListOut
from modules.storyboard.service import StoryboardService

router = APIRouter(prefix="/storyboards", tags=["分镜模板"])


def get_storyboard_service(session: SessionDep) -> StoryboardService:
    return StoryboardService(session)


StoryboardServiceDep = Annotated[StoryboardService, Depends(get_storyboard_service)]

# ---------- 权限依赖 ----------
StoryboardReadDep = Annotated[CurrentUser, Depends(require("storyboard:read"))]


@router.get("", summary="分镜模板列表")
async def list_storyboards(
    service: StoryboardServiceDep, actor: StoryboardReadDep
) -> ApiResponse[StoryboardListOut]:
    return ApiResponse.ok(await service.list_storyboards())
