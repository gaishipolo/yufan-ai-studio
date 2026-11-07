"""封装管理与调用预览的 HTTP 契约。"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from api.deps import require
from core import audit
from core.context import CurrentUser
from core.response import ApiResponse
from modules.template.pack_service import PackService
from modules.template.replacement_schema import (
    PackAction,
    PackDraftIn,
    PackManaged,
    PackManagedList,
    PackPublicList,
)

router = APIRouter(prefix="/replacement-packs", tags=["素材封装"])
ManageDep = Annotated[CurrentUser, Depends(require("asset:read", "template:pack:manage"))]
PublishDep = Annotated[
    CurrentUser, Depends(require("asset:read", "template:pack:manage", "template:pack:publish"))
]
ReadDep = Annotated[CurrentUser, Depends(require("asset:read", "template:read"))]
ServiceDep = Annotated[PackService, Depends(PackService)]


@router.get("", summary="本人封装草稿和历史包")
def managed(actor: ManageDep, service: ServiceDep) -> ApiResponse[PackManagedList]:
    return ApiResponse.ok(service.drafts(actor.id))


@router.get("/available", summary="全工作台已发布对象")
def available(
    actor: ReadDep,
    service: ServiceDep,
    kind: str = "all",
    keyword: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=200, alias="pageSize"),
) -> ApiResponse[PackPublicList]:
    return ApiResponse.ok(service.search(kind, keyword, page, page_size))


@router.put("/draft", summary="保存封装草稿（请求标识及版本防重）")
def save(payload: PackDraftIn, actor: ManageDep, service: ServiceDep) -> ApiResponse[PackManaged]:
    return ApiResponse.ok(service.save(payload, actor.id))


@router.post("/{pack_id}/publish", summary="发布不可变封装版本")
async def publish(
    pack_id: str, payload: PackAction, actor: PublishDep, service: ServiceDep
) -> ApiResponse[PackManaged]:
    # PackService 是同步文件 IO；路由改 async 只是为了能在提交后写审计
    result = await asyncio.to_thread(
        service.publish, pack_id, actor.id, payload.expected_revision
    )
    await audit.record(
        "template.pack.publish",
        actor_id=actor.id,
        target_type="media_pack",
        target_id=pack_id,
        detail={
            "revision": payload.expected_revision,
            "version": getattr(result, "version", None),
            "status": result.status,
        },
    )
    return ApiResponse.ok(result)


@router.post("/{pack_id}/disable", summary="停用已发布封装对象")
async def disable(
    pack_id: str, payload: PackAction, actor: PublishDep, service: ServiceDep
) -> ApiResponse[PackManaged]:
    result = await asyncio.to_thread(
        service.disable, pack_id, actor.id, payload.expected_revision
    )
    await audit.record(
        "template.pack.disable",
        actor_id=actor.id,
        target_type="media_pack",
        target_id=pack_id,
        detail={"revision": payload.expected_revision, "status": result.status},
    )
    return ApiResponse.ok(result)


@router.get("/{pack_id}/preview/{asset_id}", summary="读取授权发布版本的包内预览")
def preview(
    pack_id: str, asset_id: str, actor: ReadDep, service: ServiceDep, version: int = Query(ge=1)
) -> FileResponse:
    path, mime = service.preview(pack_id, asset_id, version)
    return FileResponse(path, media_type=mime)
