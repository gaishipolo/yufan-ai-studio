"""替换模板 HTTP 契约，IO 与状态规则交给 service。"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from core.response import ApiResponse
from modules.template.replacement_dependencies import (
    ReplacementReadDep,
    ReplacementServiceDep,
    ReplacementWriteDep,
)
from modules.template.replacement_schema import (
    PrepareReplacement,
    ReplacementCatalog,
    ReplacementRunOut,
)

router = APIRouter(tags=["替换模板"])


@router.get("/replacement-catalog", summary="人物/产品/场景替换模板与调用档案")
async def catalog(
    actor: ReplacementReadDep, service: ReplacementServiceDep
) -> ApiResponse[ReplacementCatalog]:
    return ApiResponse.ok(await service.catalog(actor.id))


@router.post("/replacement-runs", status_code=201, summary="归档输入并预览最终提示词，不提交生成")
async def prepare_run(
    payload: PrepareReplacement, actor: ReplacementWriteDep, service: ReplacementServiceDep
) -> ApiResponse[ReplacementRunOut]:
    return ApiResponse.ok(await service.prepare(payload, actor.id, actor.has("asset:read")))


@router.get("/replacement-runs/{run_id}", summary="替换任务状态与提示词")
async def get_run(
    run_id: str, actor: ReplacementReadDep, service: ReplacementServiceDep
) -> ApiResponse[ReplacementRunOut]:
    return ApiResponse.ok(await service.detail(run_id, actor.id))


@router.post("/replacement-runs/{run_id}/execute", summary="仅 0 积分提交或继续查询已有 LibTV 任务")
async def execute_run(
    run_id: str, actor: ReplacementWriteDep, service: ReplacementServiceDep
) -> ApiResponse[ReplacementRunOut]:
    return ApiResponse.ok(await service.execute(run_id, actor.id))


@router.get("/replacement-runs/{run_id}/output", summary="预览或下载 Z 盘原始生成结果")
async def output(
    run_id: str, actor: ReplacementReadDep, service: ReplacementServiceDep
) -> FileResponse:
    return FileResponse(await service.output(run_id, actor.id), media_type="video/mp4")
