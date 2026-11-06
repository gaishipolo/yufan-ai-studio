"""素材资产路由：只声明 HTTP 形状，装配与权限在 `dependencies.py`。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi import Path as ApiPath
from fastapi.responses import FileResponse, RedirectResponse, Response

from api.deps import PageDep
from core.media import MediaCategory
from core.response import ApiResponse
from modules.asset.archive import router as archive_router
from modules.asset.dependencies import (
    AssetCreateDep,
    AssetDeleteDep,
    AssetReadDep,
    AssetServiceDep,
)
from modules.asset.errors import AssetNotFound
from modules.asset.schema import (
    AssetListOut,
    AssetListQuery,
    AssetOut,
    AssetTypeFilter,
)

router = APIRouter(prefix="/assets", tags=["素材"])
router.include_router(archive_router)

AssetId = Annotated[str, ApiPath(min_length=1, max_length=32, description="素材 id")]


@router.get("", summary="素材列表（分页 + 类型分面计数）")
async def list_assets(
    service: AssetServiceDep,
    actor: AssetReadDep,
    page: PageDep,
    asset_type: Annotated[
        AssetTypeFilter, Query(alias="type", description="类型筛选")
    ] = AssetTypeFilter.ALL,
    keyword: Annotated[str, Query(max_length=100, description="按文件名搜索")] = "",
    category: Annotated[MediaCategory | None, Query(description="归档分类")] = None,
) -> ApiResponse[AssetListOut]:
    query = AssetListQuery(type=asset_type, keyword=keyword, category=category)
    return ApiResponse.ok(await service.list_assets(actor, query, page))


@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    summary="上传素材",
    description=(
        "请求体是文件原始字节（不是 multipart），文件名按 `?filename=` 传递。\n\n"
        "同一账号重复上传同一文件会直接返回已有记录（按 sha256 去重），不会产生第二份。"
    ),
)
async def upload_asset(
    request: Request,
    service: AssetServiceDep,
    actor: AssetCreateDep,
    filename: Annotated[str, Query(min_length=1, max_length=200)],
) -> ApiResponse[AssetOut]:
    return ApiResponse.ok(await service.upload(actor, request.stream(), filename))


@router.get("/{asset_id}", summary="素材详情")
async def get_asset(
    service: AssetServiceDep, actor: AssetReadDep, asset_id: AssetId
) -> ApiResponse[AssetOut]:
    return ApiResponse.ok(await service.get_asset(actor, asset_id))


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除素材")
async def delete_asset(
    service: AssetServiceDep, actor: AssetDeleteDep, asset_id: AssetId
) -> None:
    await service.delete_asset(actor, asset_id)


@router.get("/{asset_id}/file", summary="读取素材原文件")
async def asset_file(
    service: AssetServiceDep, actor: AssetReadDep, asset_id: AssetId
) -> Response:
    return _serve(*await service.resolve_file(actor, asset_id))


@router.get("/{asset_id}/cover", summary="读取素材缩略图")
async def asset_cover(
    service: AssetServiceDep, actor: AssetReadDep, asset_id: AssetId
) -> Response:
    return _serve(*await service.resolve_file(actor, asset_id, cover=True))


def _serve(path: Path | None, url: str | None, mime: str) -> Response:
    """本地给文件、远程给 307 重定向。

    重定向而不是把字节读进进程：素材可能是几十 MB 的视频，中转一次就是
    「应用内存 × 并发数」，而对象存储自己会处理 Range。
    """
    if path is not None:
        return FileResponse(path, media_type=mime)
    if url is None:  # pragma: no cover - service 已经保证二者必有其一
        raise AssetNotFound()
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
