"""封装管理的 HTTP 契约：草稿、发布、停用、包内预览。

路径、入参、出参与重建前一致；权限点组合是 `library:read` +
`template:pack:manage`。多带一个 `library:read` 不是冗余：封装编辑器的每一步
都要挑资产库里的图，只有 `pack:manage` 的人进得来、却选不了图。

（原先是 `asset:read`——v1 素材库已于 2026-11-13 下线，那个码不再存在。）
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.deps import require
from core import audit
from core.context import CurrentUser
from core.response import ApiResponse
from modules.template.dependencies import PackServiceDep
from modules.template.schema import (
    PackAction,
    PackDraftIn,
    PackManaged,
    PackManagedList,
    PackPublicList,
)

router = APIRouter(prefix="/replacement-packs", tags=["素材封装"])

ManageDep = Annotated[CurrentUser, Depends(require("library:read", "template:pack:manage"))]
PublishDep = Annotated[
    CurrentUser, Depends(require("library:read", "template:pack:manage", "template:pack:publish"))
]
ReadDep = Annotated[CurrentUser, Depends(require("library:read", "template:read"))]


@router.get("", summary="本人封装草稿和历史包")
async def managed(actor: ManageDep, service: PackServiceDep) -> ApiResponse[PackManagedList]:
    return ApiResponse.ok(await service.drafts(actor.id))


@router.get("/available", summary="全工作台已发布对象")
async def available(
    actor: ReadDep,
    service: PackServiceDep,
    kind: str = "all",
    keyword: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=200, alias="pageSize"),
) -> ApiResponse[PackPublicList]:
    return ApiResponse.ok(await service.search(kind, keyword, page, page_size))


@router.put("/draft", summary="保存封装草稿（请求标识及版本防重）")
async def save(
    payload: PackDraftIn, actor: ManageDep, service: PackServiceDep
) -> ApiResponse[PackManaged]:
    return ApiResponse.ok(await service.save(payload, actor.id))


@router.post("/{pack_id}/publish", summary="发布不可变封装版本")
async def publish(
    pack_id: str, payload: PackAction, actor: PublishDep, service: PackServiceDep
) -> ApiResponse[PackManaged]:
    result = await service.publish(pack_id, actor.id, payload.expected_revision)
    await audit.record(
        "template.pack.publish",
        actor_id=actor.id,
        target_type="media_pack",
        target_id=pack_id,
        detail={
            "revision": payload.expected_revision,
            "version": result.version,
            "status": result.status,
        },
    )
    return ApiResponse.ok(result)


@router.post("/{pack_id}/disable", summary="停用已发布封装对象")
async def disable(
    pack_id: str, payload: PackAction, actor: PublishDep, service: PackServiceDep
) -> ApiResponse[PackManaged]:
    result = await service.disable(pack_id, actor.id, payload.expected_revision)
    await audit.record(
        "template.pack.disable",
        actor_id=actor.id,
        target_type="media_pack",
        target_id=pack_id,
        detail={"revision": payload.expected_revision, "status": result.status},
    )
    return ApiResponse.ok(result)


@router.get("/{pack_id}/preview/{asset_id}", summary="读取授权发布版本的包内预览")
async def preview(
    pack_id: str,
    asset_id: str,
    actor: ReadDep,
    service: PackServiceDep,
    version: int = Query(ge=1),
):
    served = await service.preview(pack_id, asset_id, version)
    return served.response()
