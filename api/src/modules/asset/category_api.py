"""分类树路由：第②段分类条的数据源。

前缀 `/asset-categories` 而不是 `/assets/categories`：分类是**库的属性**，
不是素材的子资源。挂在 `/assets` 下面会让前端以为它是「素材的一个筛选项」，
而它恰恰是"先定位置"的那一级导航——路径层级会顺着被用错。

与 `/asset-tags`、`/asset-views` 同一个命名习惯（`asset-` 前缀）：
`/categories` 这个名字在别的模块里迟早会有人要用（模板分类、任务分类），
先占住通用路径，后面就得靠加参数区分，而参数一加就不再有"同一个词一个含义"。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.deps import SessionDep, require
from core.context import CurrentUser
from core.response import ApiResponse
from modules.asset.category_schema import AssetCategoryList, AssetCategoryOut
from modules.asset.library_service import LibraryService

router = APIRouter(prefix="/asset-categories")

CategoryReadDep = Annotated[CurrentUser, Depends(require("asset:read"))]


@router.get("", summary="当前库的分类树与计数")
async def list_categories(
    actor: CategoryReadDep,
    session: SessionDep,
    # `personal` / `brand` / `team:<id>`，与顶栏切换器同源。
    # 不传按个人库——新用户第一次进素材页看到的是自己的东西。
    library: str = Query(default="personal", max_length=40),
) -> ApiResponse[AssetCategoryList]:
    """返回当前库的分类树 + 每个分类的素材数。

    计数口径与 `GET /assets` 的分支一一对应（未删除 + 当前库可见）。
    两处一旦不一致，症状是「角标 149、点进去 12 条」，而这种差异
    通常被当成缓存问题排查很久。

    团队库会先校验成员关系：不是成员是 403，不是空列表——空列表会被读成
    「这个团队还没传东西」，用户会去要权限，而实际是他根本不该看。
    """
    service = LibraryService(session)
    rows = await service.categories(
        actor.id, library, visible_to_all=actor.has("asset:team:manage")
    )
    items = [
        AssetCategoryOut(
            id=row.id,
            name=row.name,
            kind=row.kind,
            is_inbox=row.is_inbox,
            sort_order=row.sort_order,
            count=count,
        )
        for row, count in rows
    ]
    inbox = sum(item.count for item in items if item.is_inbox)
    return ApiResponse.ok(AssetCategoryList(library=library, items=items, inbox_count=inbox))
