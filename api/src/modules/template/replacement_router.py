"""替换模板 HTTP 契约：准备、提交、恢复、评价、取件。

与重建前的路径、入参、出参、权限组合、限流点完全一致。**唯一的差别在服务端
实现**：状态从 `run.json` 搬进 `replacement_runs` 表，owner 过滤下沉到 SQL。

`execute` 是这套接口里最贵的入口（一次提交 = 一次真实计费），所以单独挂限流，
额度对齐生图那一档而不是普通读接口。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, Response

from api.deps import rate_limit, require
from core.config import settings
from core.context import CurrentUser
from core.response import ApiResponse
from modules.template.dependencies import ReplacementServiceDep
from modules.template.schema import (
    ExecuteReplacement,
    PrepareReplacement,
    ReconfirmReplacement,
    ReplacementCatalog,
    ReplacementReviewIn,
    ReplacementRunOut,
)

router = APIRouter(tags=["替换模板"])

ReadDep = Annotated[CurrentUser, Depends(require("template:read"))]
WriteDep = Annotated[CurrentUser, Depends(require("template:use"))]
ManageDep = Annotated[CurrentUser, Depends(require("template:read", "template:use"))]
ReviewDep = Annotated[CurrentUser, Depends(require("template:read", "template:review"))]


@router.delete(
    "/replacement-runs/{run_id}", summary="将本人替换任务与调用档案移入回收站，不取消生成"
)
async def delete_run(
    run_id: str, actor: ManageDep, service: ReplacementServiceDep
) -> ApiResponse[ReplacementRunOut]:
    return ApiResponse.ok(await service.set_deleted(run_id, actor.id, True))


@router.post("/replacement-runs/{run_id}/restore", summary="恢复本人回收站中的替换任务与调用档案")
async def restore_run(
    run_id: str, actor: ManageDep, service: ReplacementServiceDep
) -> ApiResponse[ReplacementRunOut]:
    return ApiResponse.ok(await service.set_deleted(run_id, actor.id, False))


@router.get("/replacement-catalog", summary="人物/产品/场景替换模板与调用档案")
async def catalog(
    actor: ReadDep, service: ReplacementServiceDep
) -> ApiResponse[ReplacementCatalog]:
    return ApiResponse.ok(await service.catalog(actor.id))


@router.post("/replacement-runs", status_code=201, summary="归档输入并预览最终提示词，不提交生成")
async def prepare_run(
    payload: PrepareReplacement, actor: WriteDep, service: ReplacementServiceDep
) -> ApiResponse[ReplacementRunOut]:
    return ApiResponse.ok(
        await service.prepare(payload, actor.id, can_read_assets=actor.has("asset:read"))
    )


@router.get("/replacement-runs/{run_id}", summary="替换任务状态与提示词")
async def get_run(
    run_id: str, actor: ReadDep, service: ReplacementServiceDep
) -> ApiResponse[ReplacementRunOut]:
    return ApiResponse.ok(await service.detail(run_id, actor.id))


@router.post(
    "/replacement-runs/{run_id}/execute",
    summary="提交替换任务；billingMode=credits 表示本任务用积分加速",
)
async def execute_run(
    run_id: str,
    actor: WriteDep,
    service: ReplacementServiceDep,
    payload: ExecuteReplacement | None = None,
    # 执行 = 整批镜头提交供应商（最贵的入口之一），额度对齐生图那一档
    _: None = Depends(
        rate_limit("template.replacement.execute", limit=settings.rate_limit_default_per_min)
    ),
) -> ApiResponse[ReplacementRunOut]:
    return ApiResponse.ok(
        await service.execute(run_id, actor.id, payload.billing_mode if payload else None)
    )


@router.post(
    "/replacement-runs/{run_id}/reconfirm",
    summary="确认上次提交可能已计费后放行重提（默认拒绝）",
)
async def reconfirm_run(
    run_id: str,
    payload: ReconfirmReplacement,
    actor: WriteDep,
    service: ReplacementServiceDep,
) -> ApiResponse[ReplacementRunOut]:
    return ApiResponse.ok(
        await service.reconfirm(run_id, actor.id, confirm_charged=payload.confirm_charged)
    )


@router.get("/replacement-runs/{run_id}/output", summary="预览或下载原始生成结果")
async def output(
    run_id: str, actor: ReadDep, service: ReplacementServiceDep
) -> Response:
    return (await service.output(run_id, actor.id)).response()


@router.put("/replacement-runs/{run_id}/review", summary="保存人工验收，版本冲突需重新核对")
async def review_run(
    run_id: str,
    payload: ReplacementReviewIn,
    actor: ReviewDep,
    service: ReplacementServiceDep,
) -> ApiResponse[ReplacementRunOut]:
    return ApiResponse.ok(await service.review(run_id, payload, actor.id))


@router.get("/replacement-runs/{run_id}/inputs/{asset_id}", summary="读取当次归档输入快照")
async def archived_input(
    run_id: str, asset_id: str, actor: ReadDep, service: ReplacementServiceDep
) -> FileResponse:
    return FileResponse(await service.input_file(run_id, asset_id, actor.id))


@router.get(
    "/replacement-runs/{run_id}/inputs/{asset_id}/thumbnail",
    summary="读取当次输入快照缩略图",
)
async def archived_input_thumbnail(
    run_id: str, asset_id: str, actor: ReadDep, service: ReplacementServiceDep
) -> FileResponse:
    return FileResponse(
        await service.input_preview(run_id, asset_id, actor.id), media_type="image/jpeg"
    )
