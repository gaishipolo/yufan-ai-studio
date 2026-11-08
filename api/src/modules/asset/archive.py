"""归档路由只声明 HTTP 参数和响应，业务由 ArchiveAssetService 处理。

索引在 `assets` 表（`source='archive'`），字节仍在归档盘。接口路径、入参与出参
都保持不变：前端与替换模板按 URL 契约对接，不该因为「索引从 JSON 换成 DB」
而改一行代码。

三个 `PUT` 是「我的素材」的写入口（改名 / 分享 / 回收站），彼此用不同权限点：
改名只动自己的记录，分享会让素材进入公共原片池，删除影响可用性，不能共用一个码。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from api.deps import rate_limit, require
from core.config import settings
from core.context import CurrentUser
from core.media import MediaCategory
from core.response import ApiResponse
from modules.asset.archive_dependencies import ArchiveReadDep, ArchiveServiceDep, ArchiveWriteDep
from modules.asset.archive_schema import (
    ArchiveCategory,
    ArchiveChange,
    ArchivedAsset,
    ArchiveList,
    ArchiveScope,
    ArchiveState,
    ImportAsset,
)

router = APIRouter(prefix="/archive", tags=["素材归档"])


@router.get("", summary="归档素材列表与空间占用")
async def list_archive(
    actor: ArchiveReadDep,
    service: ArchiveServiceDep,
    category: ArchiveCategory = "all",
    scope: ArchiveScope = "mine",
    keyword: str = "",
    state: ArchiveState = "all",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=200, alias="pageSize"),
) -> ApiResponse[ArchiveList]:
    return ApiResponse.ok(
        await service.list_assets(actor.id, category, scope, keyword, state, page, page_size)
    )


@router.post("/upload", status_code=201, summary="流式上传素材至归档盘")
async def upload(
    request: Request,
    actor: ArchiveWriteDep,
    service: ArchiveServiceDep,
    category: MediaCategory,
    filename: Annotated[str, Query(min_length=1, max_length=200)],
    # 一次上传按 3 次计入额度：写归档盘是重 IO，额度不该按「请求数」平等对待
    _: None = Depends(
        rate_limit(
            "asset.archive.upload", limit=settings.rate_limit_default_per_min, cost=3
        )
    ),
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.upload_for(request.stream(), filename, category, actor))


@router.post("/import", status_code=201, summary="从 Z 盘 inbox 登记现有素材")
async def import_asset(
    payload: ImportAsset,
    actor: ArchiveWriteDep,
    service: ArchiveServiceDep,
    # 导入同样要读文件 + 算校验和，按上传的同一档计
    _: None = Depends(
        rate_limit("asset.archive.import", limit=settings.rate_limit_default_per_min, cost=3)
    ),
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.import_for(payload, actor))


@router.get("/{asset_id}/file", summary="预览或下载已归档素材")
async def asset_file(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> Response:
    return (await service.file(asset_id, actor.id)).response()


@router.get("/{asset_id}/thumbnail", summary="读取归档素材缩略图")
async def asset_thumbnail(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> Response:
    return (await service.file(asset_id, actor.id, preview=True)).response()


@router.get("/{asset_id}/stream", summary="浏览器兼容视频预览（派生文件，不改原片）")
async def asset_stream(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> Response:
    # 远端驱动下这里返回 307：`Range` 由对象存储处理，首帧不用等整个文件
    return (await service.stream(asset_id, actor.id)).response()


@router.get("/{asset_id}", summary="读取本人或共享原片详情")
async def archive_detail(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.detail(asset_id, actor.id))


@router.get("/{asset_id}/preview", summary="受控公共原片预览")
async def public_preview(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> Response:
    return (await service.preview(asset_id, actor.id)).response()


UpdateDep = Annotated[CurrentUser, Depends(require("asset:update"))]
ShareDep = Annotated[CurrentUser, Depends(require("asset:share"))]
DeleteDep = Annotated[CurrentUser, Depends(require("asset:delete"))]


@router.put("/{asset_id}/name", summary="修改本人素材名称")
async def rename(
    asset_id: str, payload: ArchiveChange, actor: UpdateDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.change_action(asset_id, actor.id, payload, "name"))


@router.put("/{asset_id}/sharing", summary="分享或取消分享本人原片")
async def sharing(
    asset_id: str, payload: ArchiveChange, actor: ShareDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.change_action(asset_id, actor.id, payload, "shared"))


@router.put("/{asset_id}/deleted", summary="将本人素材移入回收站或恢复")
async def deleted(
    asset_id: str, payload: ArchiveChange, actor: DeleteDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.change_action(asset_id, actor.id, payload, "deleted"))


@router.delete("/{asset_id}", status_code=204, summary="彻底删除回收站中的归档素材")
async def purge(asset_id: str, actor: DeleteDep, service: ArchiveServiceDep) -> Response:
    """清空单条回收站记录。与 `PUT /deleted` 分成两步：软删可恢复，真删不可逆。"""
    await service.purge(asset_id, actor.id)
    return Response(status_code=204)
