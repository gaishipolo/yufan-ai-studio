"""素材模块的依赖装配与权限依赖。

从 `router.py` 里搬出来的原因不是"文件短一点"，而是：路由文件应该只有 HTTP 形状
（路径、查询参数、状态码），依赖怎么拼属于装配层。两者混在一起时，加一个接口
就得先读懂一堆 `Annotated[...]` 才能确认权限点有没有漏。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import SessionDep, StorageDep, require
from core.context import CurrentUser
from modules.asset.service import AssetService


def get_asset_service(session: SessionDep, storage: StorageDep) -> AssetService:
    return AssetService(session, storage)


AssetServiceDep = Annotated[AssetService, Depends(get_asset_service)]

# ---------- 权限依赖（权限点由本模块声明，核心不登记）----------
AssetReadDep = Annotated[CurrentUser, Depends(require("asset:read"))]
AssetCreateDep = Annotated[CurrentUser, Depends(require("asset:create"))]
AssetDeleteDep = Annotated[CurrentUser, Depends(require("asset:delete"))]

__all__ = [
    "AssetCreateDep",
    "AssetDeleteDep",
    "AssetReadDep",
    "AssetServiceDep",
    "get_asset_service",
]
