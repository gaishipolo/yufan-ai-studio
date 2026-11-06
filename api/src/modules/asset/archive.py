"""归档路由只声明 HTTP 参数和响应，业务由 ArchiveAssetService 处理。

索引在 `assets` 表（`source='archive'`），字节仍在归档盘。接口路径、入参与出参
都保持不变：前端与替换模板按 URL 契约对接，不该因为「索引从 JSON 换成 DB」
而改一行代码。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from core.media import MediaCategory
from core.response import ApiResponse
from modules.asset.archive_dependencies import ArchiveReadDep, ArchiveServiceDep, ArchiveWriteDep
from modules.asset.archive_schema import ArchivedAsset, ArchiveList, ImportAsset

router = APIRouter(prefix="/archive", tags=["素材归档"])


@router.get("", summary="归档素材列表与空间占用")
async def list_archive(
    actor: ArchiveReadDep, service: ArchiveServiceDep, category: str = "all"
) -> ApiResponse[ArchiveList]:
    return ApiResponse.ok(await service.list_assets(actor.id, category))


@router.post("/upload", status_code=201, summary="流式上传素材至归档盘")
async def upload(
    request: Request,
    actor: ArchiveWriteDep,
    service: ArchiveServiceDep,
    category: MediaCategory,
    filename: Annotated[str, Query(min_length=1, max_length=200)],
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.upload(request.stream(), filename, category, actor.id))


@router.post("/import", status_code=201, summary="从 Z 盘 inbox 登记现有素材")
async def import_asset(
    payload: ImportAsset, actor: ArchiveWriteDep, service: ArchiveServiceDep
) -> ApiResponse[ArchivedAsset]:
    return ApiResponse.ok(await service.import_asset(payload, actor.id))


@router.get("/{asset_id}/file", summary="预览或下载已归档素材")
async def asset_file(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> FileResponse:
    path, mime = await service.file(asset_id, actor.id)
    return FileResponse(path, media_type=mime)


@router.get("/{asset_id}/thumbnail", summary="读取归档素材缩略图")
async def asset_thumbnail(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> FileResponse:
    path, mime = await service.file(asset_id, actor.id, preview=True)
    return FileResponse(path, media_type=mime)


@router.get("/{asset_id}/stream", summary="浏览器兼容视频预览（派生文件，不改原片）")
async def asset_stream(
    asset_id: str, actor: ArchiveReadDep, service: ArchiveServiceDep
) -> FileResponse:
    path, mime = await service.stream(asset_id, actor.id)
    return FileResponse(path, media_type=mime)
