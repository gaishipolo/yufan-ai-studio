"""资产库 v2 路由，统一前缀 `/library`。

**与现役 `/assets` 完全隔离**：表前缀、权限码、注入口、前端路径全部独立，
切换时只改前端模块的 `load()` 一行，后端两套可以同时在线。

M1 交付「个人库」竖线：列表 / 分面 / 上传 / 源视频上传 / 详情 / 取件。
共享、草稿、回收站、封装、下载、生成入库在 M2/M3，路由不提前占位。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response

from api.deps import PageDep, rate_limit
from core.config import settings
from core.ports.library_lookup import LibraryAssetKind, LibraryAssetState
from core.response import ApiResponse
from modules.library.dependencies import (
    LibraryCreateDep,
    LibraryReadDep,
    LibraryServiceDep,
)
from modules.library.schema import (
    AssetSort,
    LibraryAssetDetailOut,
    LibraryAssetOut,
    LibraryAssetPage,
    LibraryFacets,
    LibraryTab,
    UploadKind,
)

router = APIRouter(prefix="/library", tags=["资产库"])


@router.get("/assets", summary="资产库列表（分页 + 分面）")
async def list_assets(
    actor: LibraryReadDep,
    page: PageDep,
    service: LibraryServiceDep,
    library: LibraryTab = LibraryTab.ALL,
    state: LibraryAssetState = LibraryAssetState.ACTIVE,
    kind: LibraryAssetKind | None = None,
    tag_ids: Annotated[list[str] | None, Query(alias="tagIds")] = None,
    keyword: str = Query(default="", max_length=100),
    sort: AssetSort = AssetSort.CREATED_DESC,
) -> ApiResponse[LibraryAssetPage]:
    return ApiResponse.ok(
        await service.list_assets(
            actor,
            page,
            library=library,
            state=state.value,
            kind=kind.value if kind else None,
            tag_ids=tag_ids,
            keyword=keyword,
            sort=sort,
        )
    )


@router.get("/facets", summary="资产库分面统计")
async def facets(actor: LibraryReadDep, service: LibraryServiceDep) -> ApiResponse[LibraryFacets]:
    """给「不加载列表也要显示角标」的页面用（侧栏、首页卡片）。"""
    return ApiResponse.ok(await service.facets(actor))


@router.post(
    "/assets/upload",
    status_code=status.HTTP_201_CREATED,
    summary="上传图片 / 音频（流式，按内容秒传）",
    dependencies=[
        # 上传是重 IO：一次按 3 次计入额度，与旧素材库同一档
        Depends(
            rate_limit(
                "library.upload", limit=settings.rate_limit_default_per_min, cost=3
            )
        )
    ],
)
async def upload(
    request: Request,
    actor: LibraryCreateDep,
    service: LibraryServiceDep,
    filename: Annotated[str, Query(min_length=1, max_length=200)],
    kind: UploadKind = UploadKind.IMAGE,
    source_key: Annotated[str | None, Query(alias="sourceKey", max_length=160)] = None,
) -> ApiResponse[LibraryAssetOut]:
    """只收图片与音频：视频走 `/source-videos`（模板库专用）。

    分开的理由不是「类型不同」，而是**来源语义不同**：这个接口是用户自己挑文件，
    视频上传属于模板库的参考素材流程，它需要 `origin=template_source` 才能在
    切换后继续喂给模板封装。混成一个接口会让来源变成前端传参，写错了没人发现。
    """
    return ApiResponse.ok(
        await service.upload(
            request.stream(),
            filename,
            kind=kind,
            actor=actor,
            origin="upload",
            source_key=source_key,
        )
    )


@router.post(
    "/source-videos",
    status_code=status.HTTP_201_CREATED,
    summary="上传模板库参考视频（只收视频）",
    dependencies=[
        Depends(
            rate_limit(
                "library.source_videos",
                limit=settings.rate_limit_default_per_min,
                cost=3,
            )
        )
    ],
)
async def upload_source_video(
    request: Request,
    actor: LibraryCreateDep,
    service: LibraryServiceDep,
    filename: Annotated[str, Query(min_length=1, max_length=200)],
    source_key: Annotated[str | None, Query(alias="sourceKey", max_length=160)] = None,
) -> ApiResponse[LibraryAssetOut]:
    """等价于旧 `POST /assets/upload?category=source`，只是路径与来源不同。

    模板库前端切换时只改 `archive.api.ts` 里一个 URL 常量，界面一行不动。
    """
    return ApiResponse.ok(
        await service.upload(
            request.stream(),
            filename,
            kind=UploadKind.VIDEO,
            actor=actor,
            origin="template_source",
            source_key=source_key,
        )
    )


@router.get("/assets/{asset_id}", summary="资产详情（含标签与生成参数）")
async def asset_detail(
    asset_id: str, actor: LibraryReadDep, service: LibraryServiceDep
) -> ApiResponse[LibraryAssetDetailOut]:
    return ApiResponse.ok(await service.detail(asset_id, actor))


@router.get("/assets/{asset_id}/file", summary="下载或预览原文件")
async def asset_file(
    asset_id: str, actor: LibraryReadDep, service: LibraryServiceDep
) -> Response:
    return (await service.file(asset_id, actor)).response()


@router.get("/assets/{asset_id}/thumbnail", summary="读取缩略图")
async def asset_thumbnail(
    asset_id: str, actor: LibraryReadDep, service: LibraryServiceDep
) -> Response:
    return (await service.file(asset_id, actor, preview=True)).response()
