"""素材资产路由：只声明 HTTP 形状，装配与权限在 `dependencies.py`。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi import Path as ApiPath
from fastapi.responses import Response

from api.deps import PageDep, rate_limit
from core.config import settings
from core.media import MediaCategory
from core.response import ApiResponse
from modules.asset.archive import router as archive_router
from modules.asset.dependencies import (
    AssetCreateDep,
    AssetDeleteDep,
    AssetReadDep,
    AssetServiceDep,
)
from modules.asset.schema import (
    AssetListOut,
    AssetListQuery,
    AssetOut,
    AssetTypeFilter,
)
from modules.asset.serving import serve

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
    # 流式上传：单请求的 IO 与磁盘占用远高于普通写接口，按 3 次计入额度
    _: None = Depends(
        rate_limit("asset.upload", limit=settings.rate_limit_default_per_min, cost=3)
    ),
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
    return serve(*await service.resolve_file(actor, asset_id))


@router.get("/{asset_id}/cover", summary="读取素材缩略图")
async def asset_cover(
    service: AssetServiceDep, actor: AssetReadDep, asset_id: AssetId
) -> Response:
    return serve(*await service.resolve_file(actor, asset_id, cover=True))
