"""素材资产路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from api.deps import SessionDep, StorageDep, require
from core.context import CurrentUser
from core.exceptions import NotImplementedFeature
from core.response import ApiResponse
from modules.asset.schema import (
    AssetListOut,
    AssetListQuery,
    AssetTypeFilter,
    UploadAssetIn,
    UploadTicketOut,
)
from modules.asset.service import AssetService

router = APIRouter(prefix="/assets", tags=["素材"])


def get_asset_service(session: SessionDep) -> AssetService:
    return AssetService(session)


AssetServiceDep = Annotated[AssetService, Depends(get_asset_service)]

# ---------- 权限依赖（权限点由本模块声明，核心不登记）----------
AssetReadDep = Annotated[CurrentUser, Depends(require("asset:read"))]
AssetCreateDep = Annotated[CurrentUser, Depends(require("asset:create"))]
AssetDeleteDep = Annotated[CurrentUser, Depends(require("asset:delete"))]


@router.get("", summary="素材列表（按类型分面计数）")
async def list_assets(
    service: AssetServiceDep,
    actor: AssetReadDep,
    asset_type: Annotated[
        AssetTypeFilter, Query(alias="type", description="类型筛选")
    ] = AssetTypeFilter.ALL,
    keyword: Annotated[str, Query(max_length=100)] = "",
) -> ApiResponse[AssetListOut]:
    query = AssetListQuery(type=asset_type, keyword=keyword)
    return ApiResponse.ok(await service.list_assets(actor, query))


@router.post(
    "/upload-ticket",
    status_code=201,
    summary="申请上传凭证（待实现）",
)
async def create_upload_ticket(
    service: AssetServiceDep,
    actor: AssetCreateDep,
    storage: StorageDep,
    payload: UploadAssetIn,
) -> ApiResponse[UploadTicketOut]:
    ticket = await service.create_upload_ticket(actor, payload, storage)
    return ApiResponse.ok(ticket)


@router.delete("/{asset_id}", status_code=204, summary="删除素材（待实现）")
async def delete_asset(
    service: AssetServiceDep, actor: AssetDeleteDep, asset_id: Annotated[str, Path()]
) -> None:
    await service.delete_asset(actor, asset_id)


@router.get("/{asset_id}", summary="素材详情（待实现）")
async def get_asset(
    service: AssetServiceDep, actor: AssetReadDep, asset_id: Annotated[str, Path()]
) -> ApiResponse[None]:
    # TODO: 返回单条素材详情
    raise NotImplementedFeature("素材详情接口待实现")
