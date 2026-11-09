"""团队路由：顶栏库切换器的数据源。

前缀 `/teams` 而不是 `/assets/teams`：团队是**库的边界**，不是素材的子资源。
挂在 `/assets` 下面会让前端把它当成「素材的一个筛选项」，而它恰恰是切换器的
一级导航——路径层级会顺着被用错。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from api.deps import SessionDep, require
from core.context import CurrentUser
from core.response import ApiResponse
from modules.asset.library_service import LibraryService
from modules.asset.team_schema import TeamList, TeamOut

router = APIRouter(prefix="/teams")

TeamReadDep = Annotated[CurrentUser, Depends(require("asset:team:read"))]


@router.get("", summary="当前用户可见的团队（库切换器数据源）")
async def list_teams(
    actor: TeamReadDep, session: SessionDep
) -> ApiResponse[TeamList]:
    """可见范围：有 `asset:team:manage` 的人看全部，其余人只看自己所在的团队。

    **跨团队不可见是默认行为**，不是 M5 才生效的策略：三个团队并存时，
    「能看见但不能改」会让每个团队都变成别人的参考库，边界形同虚设。
    要跨团队共享走「提升到公共库」那条路，那条路有审核也有审计。
    """
    can_manage = actor.has("asset:team:manage")
    rows = await LibraryService(session).teams_for(actor.id, visible_to_all=can_manage)
    return ApiResponse.ok(
        TeamList(
            items=[
                TeamOut(id=team.id, name=team.name, kind=team.kind, role=role, member_count=count)
                for team, role, count in rows
            ],
            can_manage=can_manage,
        )
    )
