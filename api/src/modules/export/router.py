"""导出路由：只做协议转换。

注意：每个接口显式声明所需权限码，权限点在本模块 `__init__.py` 里登记，
核心不感知。

**路由顺序有讲究**：`/sources` 必须写在 `/{export_id}` 之前。FastAPI 按注册
顺序匹配，反过来的话 `GET /exports/sources` 会被当成「id 为 sources 的详情」，
表现为一个莫名其妙的 404——而且日志里看不出任何异常。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from api.deps import PageDep, rate_limit
from core.response import ApiResponse, Page
from modules.export.dependencies import (
    ExportCreateDep,
    ExportDeleteDep,
    ExportReadDep,
    ExportServiceDep,
)
from modules.export.schema import (
    CreateExportIn,
    ExportDetailOut,
    ExportOut,
    ExportSourceOut,
)
from modules.export.sources import available_sources

router = APIRouter(prefix="/exports", tags=["导出"])


@router.get("", summary="我的导出记录")
async def list_exports(
    service: ExportServiceDep,
    actor: ExportReadDep,
    page: PageDep,
    export_status: Annotated[
        str | None,
        Query(alias="status", description="状态筛选：queued/running/succeeded/failed/…"),
    ] = None,
    data_source: Annotated[
        str | None, Query(description="数据源筛选")
    ] = None,
) -> ApiResponse[Page[ExportOut]]:
    # 数据范围固定为「自己的」：不加 read:all，别人的导出与本事无关
    return ApiResponse.ok(
        await service.list_items(
            actor, page, status=export_status, data_source=data_source
        )
    )


@router.get("/sources", summary="可导出的数据源")
async def list_sources(actor: ExportReadDep) -> ApiResponse[list[ExportSourceOut]]:
    return ApiResponse.ok(
        [ExportSourceOut.model_validate(item) for item in available_sources(actor)]
    )


# 写接口默认挂限流：漏挂等于完全不限（限流是依赖级，没有全局兜底）
@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="创建导出（异步生成，202）",
    dependencies=[Depends(rate_limit("export.create", limit=10))],
)
async def create_export(
    service: ExportServiceDep,
    actor: ExportCreateDep,
    payload: CreateExportIn,
) -> ApiResponse[ExportOut]:
    # 202 而不是 201：这一刻只有一条排队记录，文件还不存在。
    # 返回 201 会让客户端以为可以立刻下载。
    return ApiResponse.ok(await service.create(actor, payload))


@router.get("/{export_id}", summary="导出详情")
async def get_export(
    export_id: str, service: ExportServiceDep, actor: ExportReadDep
) -> ApiResponse[ExportDetailOut]:
    return ApiResponse.ok(await service.detail(actor, export_id))


@router.get(
    "/{export_id}/download",
    summary="下载导出文件（本地驱动直出，远端驱动 307）",
    response_class=Response,
    responses={
        200: {"content": {"text/csv": {}}, "description": "CSV 文件（本地驱动）"},
        307: {"description": "重定向到对象存储的预签名地址（远端驱动）"},
    },
)
async def download_export(
    export_id: str, service: ExportServiceDep, actor: ExportReadDep
) -> Response:
    served = await service.download(actor, export_id)
    return served.response()


@router.delete(
    "/{export_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除导出（连带产物）",
    dependencies=[Depends(rate_limit("export.delete", limit=30))],
)
async def delete_export(
    export_id: str, service: ExportServiceDep, actor: ExportDeleteDep
) -> Response:
    await service.delete(actor, export_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
