"""资产库 v2 路由，统一前缀 `/library`。

**与现役 `/assets` 完全隔离**：表前缀、权限码、注入口、前端路径全部独立，
切换时只改前端模块的 `load()` 一行，后端两套可以同时在线。

M0 只有一条列表接口，返回空页与零分面：它是五个一级入口共用的壳，
先把契约（分页 + 分面 + 过滤参数）定下来，M1 建表后把实现换成真实查询即可，
前端不必再改请求形状。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from api.deps import PageDep
from core.ports.library_lookup import LibraryAssetKind, LibraryAssetState
from core.response import ApiResponse
from modules.library.dependencies import LibraryReadDep
from modules.library.schema import AssetSort, LibraryAssetPage, LibraryFacets, LibraryTab

router = APIRouter(prefix="/library", tags=["资产库"])


@router.get("/assets", summary="资产库列表（分页 + 分面）")
async def list_assets(
    _actor: LibraryReadDep,
    page: PageDep,
    library: LibraryTab = LibraryTab.ALL,
    state: LibraryAssetState = LibraryAssetState.ACTIVE,
    kind: LibraryAssetKind | None = None,
    tag_ids: Annotated[list[str] | None, Query(alias="tagIds")] = None,
    keyword: str = Query(default="", max_length=100),
    sort: AssetSort = AssetSort.CREATED_DESC,
) -> ApiResponse[LibraryAssetPage]:
    """列表。

    M0 骨架：过滤参数已进契约，但还没有表可查，所以固定返回空页。
    这里刻意返回 200 而不是 501 —— 前端占位页与「真实空库」需要同一条渲染路径，
    返回 501 会逼前端为「骨架期」写一个临时分支，M1 又要删掉。
    """
    # M0 骨架：过滤参数已进契约但还没有表可查，M1 接表后换成真实过滤。
    return ApiResponse.ok(LibraryAssetPage.empty(page))


@router.get("/facets", summary="资产库分面统计")
async def facets(_actor: LibraryReadDep) -> ApiResponse[LibraryFacets]:
    """单独的分面接口：给「不加载列表也要显示角标」的页面用（如首页卡片）。

    列表接口已经带 `facets`，这个接口是给侧栏常驻角标用的，避免为了六个数字
    拉一整页数据。
    """
    return ApiResponse.ok(LibraryFacets())
