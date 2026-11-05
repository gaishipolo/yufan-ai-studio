"""模型目录路由：前端「模型选择器」的唯一数据源。

新增供应商模型 → 这里自动出现，前端不需要发版。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.deps import require
from core.context import CurrentUser
from core.response import ApiResponse
from infra import get_model_router
from infra.ai.base import Capability
from modules.catalog.schema import ModelListOut
from modules.catalog.service import CatalogService

router = APIRouter(prefix="/models", tags=["模型"])


def get_catalog_service() -> CatalogService:
    return CatalogService(get_model_router())


CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]

# ---------- 权限依赖 ----------
CatalogReadDep = Annotated[CurrentUser, Depends(require("catalog:read"))]


@router.get("", summary="可用模型目录（含参数 schema）")
async def list_models(
    service: CatalogServiceDep,
    actor: CatalogReadDep,
    capability: Annotated[
        Capability | None, Query(description="能力筛选：video / image / audio")
    ] = None,
) -> ApiResponse[ModelListOut]:
    return ApiResponse.ok(service.list_models(capability))
