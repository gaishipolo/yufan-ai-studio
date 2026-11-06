"""归档路由只声明 HTTP 参数和响应，业务由 ArchiveAssetService 处理。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse

from api.deps import require
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
def list_archive(
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
        service.list_assets(actor.id, category, scope, keyword, state, page, page_size)
    )


@router.post("/upload", status_code=201, summary="流式上传素材至归档盘")
async def upload(
    request: Request,
    actor: ArchiveWriteDep,
    service: ArchiveServiceDep,
    category: MediaCategory,
    filename: Annotated[str, Query(min_length=1, max_length=200)],
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.upload_for(request.stream(), filename, category, actor))


@router.post("/import", status_code=201, summary="从 Z 盘 inbox 登记现有素材")
def import_asset(
    payload: ImportAsset, actor: ArchiveWriteDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(service.import_for(payload, actor))


@router.get("/{asset_id}/file", summary="预览或下载已归档素材")
def asset_file(asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep) -> FileResponse:
    path, mime = service.file(asset_id, actor.id)
    return FileResponse(path, media_type=mime)


@router.get("/{asset_id}/thumbnail", summary="读取归档素材缩略图")
def asset_thumbnail(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> FileResponse:
    path, mime = service.file(asset_id, actor.id, preview=True)
    return FileResponse(path, media_type=mime)


@router.get("/{asset_id}/stream", summary="浏览器兼容视频预览（派生文件，不改原片）")
def asset_stream(asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep) -> FileResponse:
    path, mime = service.stream(asset_id, actor.id)
    return FileResponse(path, media_type=mime)


@router.get("/{asset_id}", summary="读取本人或共享原片详情")
def archive_detail(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(service.detail(asset_id, actor.id))


@router.get("/{asset_id}/preview", summary="受控公共原片预览")
def public_preview(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> FileResponse:
    path, mime = service.preview(asset_id, actor.id)
    return FileResponse(path, media_type=mime)


UpdateDep = Annotated[CurrentUser, Depends(require("asset:update"))]
ShareDep = Annotated[CurrentUser, Depends(require("asset:share"))]
DeleteDep = Annotated[CurrentUser, Depends(require("asset:delete"))]


@router.put("/{asset_id}/name", summary="修改本人素材名称")
def rename(
    asset_id: str, payload: ArchiveChange, actor: UpdateDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(service.change_action(asset_id, actor.id, payload, "name"))


@router.put("/{asset_id}/sharing", summary="分享或取消分享本人原片")
def sharing(
    asset_id: str, payload: ArchiveChange, actor: ShareDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(service.change_action(asset_id, actor.id, payload, "shared"))


@router.put("/{asset_id}/deleted", summary="将本人素材移入回收站或恢复")
def deleted(
    asset_id: str, payload: ArchiveChange, actor: DeleteDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(service.change_action(asset_id, actor.id, payload, "deleted"))
