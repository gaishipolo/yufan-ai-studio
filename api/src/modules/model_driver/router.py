"""模型驱动模块管理面：驱动清单、模型目录、任务提交/查询/取消/重试。

「下游其他模块调用生成」不经过这里，走 `core/ports/model_generation` 注入口；
这组 REST 是给运维与页面看状态用的，以及承载「提交结果未知」的人工重试出口。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.deps import rate_limit, require
from core.config import settings
from core.context import CurrentUser
from core.exceptions import NotFound
from core.ports.model_generation import GenerationTicket, SubmitSpec
from core.response import ApiResponse
from modules.model_driver.registry import driver_summaries, load_errors
from modules.model_driver.schema import (
    DriverListOut,
    DriverOut,
    ModelListOut,
    ModelOut,
    SubmitIn,
    TaskOut,
)
from modules.model_driver.service import ModelGenerationFacade

router = APIRouter(prefix="/model-driver", tags=["模型驱动"])


def get_facade() -> ModelGenerationFacade:
    return ModelGenerationFacade()


FacadeDep = Annotated[ModelGenerationFacade, Depends(get_facade)]
DriverReadDep = Annotated[CurrentUser, Depends(require("model_driver:read"))]
DriverCreateDep = Annotated[CurrentUser, Depends(require("model_driver:create"))]
DriverCancelDep = Annotated[CurrentUser, Depends(require("model_driver:cancel"))]


def _task_out(ticket: GenerationTicket) -> TaskOut:
    return TaskOut(
        id=ticket.task_id,
        model=ticket.model,
        capability=ticket.capability,
        status=ticket.status,
        progress=ticket.progress,
        output_url=ticket.output_url,
        cover_url=ticket.cover_url,
        error=ticket.error,
        external_id=ticket.external_id,
        owner_id=ticket.owner_id,
        source=ticket.source,
        submit_attempts=ticket.submit_attempts,
        created_at=ticket.created_at,
    )


@router.get("/drivers", summary="已自动载入的模型驱动清单")
async def list_drivers(actor: DriverReadDep) -> ApiResponse[DriverListOut]:
    infos = driver_summaries()
    items = [
        DriverOut(
            name=item.name,
            label=item.label,
            version=item.version,
            path=item.path,
            capabilities=list(item.capabilities),
            model_count=item.model_count,
            supports_poll=item.supports_poll,
            loaded=item.loaded,
            error=item.error,
        )
        for item in infos
    ]
    return ApiResponse.ok(DriverListOut(items=items, total=len(items), errors=list(load_errors())))


@router.get("/models", summary="统一生成接口可用的模型目录")
async def list_models(
    facade: FacadeDep,
    actor: DriverReadDep,
    capability: Annotated[str, Query(description="能力筛选：video / image / audio")] = "",
) -> ApiResponse[ModelListOut]:
    items = [
        ModelOut(
            id=model.id,
            label=model.label,
            capability=model.capability,
            provider=model.provider,
            description=model.description,
            param_schema=dict(model.param_schema),
            max_references=model.max_references,
        )
        for model in facade.models(capability)
    ]
    return ApiResponse.ok(ModelListOut(items=items, total=len(items)))


@router.post("/tasks", summary="提交一次统一生成任务")
async def submit_task(
    payload: SubmitIn,
    facade: FacadeDep,
    actor: DriverCreateDep,
    _: None = Depends(rate_limit("model_driver.submit", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[TaskOut]:
    ticket = await facade.submit(
        SubmitSpec(
            model=payload.model,
            prompt=payload.prompt,
            owner_id=actor.id,
            params=payload.params,
            reference_urls=tuple(payload.reference_urls),
            name=payload.name,
            source=payload.source,
        )
    )
    return ApiResponse.ok(_task_out(ticket))


@router.get("/tasks/{task_id}", summary="查询统一生成任务")
async def get_task(
    task_id: str, facade: FacadeDep, actor: DriverReadDep
) -> ApiResponse[TaskOut]:
    ticket = await facade.query(task_id)
    if ticket is None:
        raise NotFound("生成任务不存在")
    return ApiResponse.ok(_task_out(ticket))


@router.post("/tasks/{task_id}/cancel", summary="取消生成任务")
async def cancel_task(
    task_id: str,
    facade: FacadeDep,
    actor: DriverCancelDep,
    _: None = Depends(rate_limit("model_driver.cancel", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[TaskOut]:
    ticket = await facade.cancel(task_id)
    if ticket is None:
        raise NotFound("生成任务不存在")
    return ApiResponse.ok(_task_out(ticket))


@router.post("/tasks/{task_id}/retry", summary="人工重试失败的生成任务")
async def retry_task(
    task_id: str,
    facade: FacadeDep,
    actor: DriverCreateDep,
    confirm_charged: Annotated[
        bool, Query(alias="confirmCharged", description="确认供应商可能已计费")
    ] = False,
    _: None = Depends(rate_limit("model_driver.retry", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[TaskOut]:
    ticket = await facade.retry(task_id, confirm_charged=confirm_charged)
    return ApiResponse.ok(_task_out(ticket))
