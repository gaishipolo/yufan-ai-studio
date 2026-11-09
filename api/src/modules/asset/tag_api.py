"""标签与保存视图的路由。

前缀分三条：`/asset-tags`（词表）、`/asset-views`（保存视图）。
素材自身的标签写入挂在 `/assets/{id}/tags`（`api.py`）——它改的是一条素材，
放在素材路由下才不会让人以为标签是可以独立存在的资源。

**两个权限点，不是四个**：
- 读词表与读写自己的视图都要 `asset:read`。视图是"个人的筛选条件"，
  给它单开一个码等于承认「能看素材但不能存筛选」，那不是一个真实的能力边界；
- 建词要 `asset:tag:manage`。词表是全局配置，一个词的错误会影响所有人。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from api.deps import SessionDep, rate_limit, require
from core.context import CurrentUser
from core.response import ApiResponse
from modules.asset.tag_schema import (
    AssetViewCreate,
    AssetViewList,
    AssetViewOut,
    TagCatalog,
    TagCreate,
    TagGroup,
    TagOut,
)
from modules.asset.tag_service import PINNED_THRESHOLD, TagService, dimension_label

router = APIRouter(prefix="/asset-tags")
views_router = APIRouter(prefix="/asset-views")

TagReadDep = Annotated[CurrentUser, Depends(require("asset:read"))]
TagManageDep = Annotated[CurrentUser, Depends(require("asset:tag:manage"))]


def _tag_out(tag, use_count: int = 0) -> TagOut:
    return TagOut(
        id=tag.id,
        dimension=tag.dimension,
        name=tag.name,
        status=tag.status,
        source=tag.source,
        use_count=use_count,
        pinned=use_count >= PINNED_THRESHOLD,
    )


@router.get("", summary="标签词表（按维度分组，带在用数量）")
async def list_tags(actor: TagReadDep, session: SessionDep) -> ApiResponse[TagCatalog]:
    """筛选条的数据源。

    `useCount` 只数未删除的素材：回收站里的关联保留（恢复后标签要回来），
    但不计入热度——否则一个词能靠一堆已删除的素材把自己顶进常显区。
    """
    groups = await TagService(session).catalog()
    items = [
        TagGroup(
            dimension=dimension,
            label=dimension_label(dimension),
            items=[_tag_out(tag, count) for tag, count in pairs],
        )
        for dimension, pairs in groups
    ]
    return ApiResponse.ok(
        TagCatalog(
            groups=items,
            total=sum(len(group.items) for group in items),
            pinned_threshold=PINNED_THRESHOLD,
        )
    )


@router.post("", status_code=201, summary="新增标签（词表外的新词）")
async def create_tag(
    payload: TagCreate,
    actor: TagManageDep,
    session: SessionDep,
    # 词表是全局配置，一次误操作会影响所有人：额度按"人工新增"的量级给
    _: None = Depends(rate_limit("asset.tags.create", limit=20)),
) -> ApiResponse[TagOut]:
    """新增即生效、不审批，但记 `created_by`。

    **准入三问在表单上，不在接口里**：接口只能挡住"同名词"和"维度不存在"，
    挡不住"这个概念两个人看着会给出不同答案"。所以这条路径要窄——只有管理员
    能进，且每次新增都留痕，便于季度复查时把零使用的词收掉。
    """
    tag = await TagService(session).create(payload.dimension, payload.name, actor.id)
    return ApiResponse.ok(_tag_out(tag))


@views_router.get("", summary="我的保存视图")
async def list_views(actor: TagReadDep, session: SessionDep) -> ApiResponse[AssetViewList]:
    service = TagService(session)
    rows = await service.list_views(actor.id)
    return ApiResponse.ok(
        AssetViewList(
            items=[
                AssetViewOut(
                    id=row.id,
                    name=row.name,
                    query=await service.view_query(row),
                    sort=row.sort,
                    created_at=row.created_at,
                )
                for row in rows
            ]
        )
    )


@views_router.post("", status_code=201, summary="保存当前筛选条件")
async def save_view(
    payload: AssetViewCreate,
    actor: TagReadDep,
    session: SessionDep,
    _: None = Depends(rate_limit("asset.views.save", limit=30)),
) -> ApiResponse[AssetViewOut]:
    """存的是**筛选参数**，不是结果集。

    存结果集的视图会在素材增减后给出过期的列表——用户以为"这个视图里就这些"，
    而实际上它是某个时刻的快照。参数是幂等的，结果不是。
    """
    service = TagService(session)
    row = await service.save_view(actor.id, payload.name, payload.query, payload.sort)
    return ApiResponse.ok(
        AssetViewOut(
            id=row.id,
            name=row.name,
            query=await service.view_query(row),
            sort=row.sort,
            created_at=row.created_at,
        )
    )


@views_router.delete("/{view_id}", status_code=204, summary="删除我的保存视图")
async def delete_view(
    view_id: str,
    actor: TagReadDep,
    session: SessionDep,
    _: None = Depends(rate_limit("asset.views.delete", limit=30)),
) -> None:
    await TagService(session).delete_view(actor.id, view_id)
