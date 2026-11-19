"""模型驱动模块管理面：驱动清单、模型目录、任务提交/查询/取消/重试。

「下游其他模块调用生成」不经过这里，走 `core/ports/model_generation` 注入口；
这组 REST 是给运维与页面看状态用的，以及承载「提交结果未知」的人工重试出口。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from api.deps import rate_limit, require
from core.config import settings
from core.context import CurrentUser
from core.exceptions import NotFound
from core.ports.model_generation import GenerationTicket, SubmitSpec
from core.response import ApiResponse
from modules.model_driver.libtv import account as libtv_api
from modules.model_driver.registry import driver_summaries, load_errors
from modules.model_driver.schema import (
    DriverListOut,
    DriverOut,
    LibTvAccountOut,
    LibTvAssetOut,
    LibTvBenefitOut,
    LibTvConsumptionItemOut,
    LibTvConsumptionOut,
    LibTvProgressIn,
    LibTvProgressOut,
    LibTvQueueItemOut,
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
#: 排队位次是**任务**的属性（用户看自己的任务为什么慢），不是驱动管理动作：
#: 用 `task:read` 而不是 `model_driver:read`，否则只有超管看得到 —— 而
#: `model_driver:read` 在种子角色里没有任何一个角色拥有。
TaskReadDep = Annotated[CurrentUser, Depends(require("task:read"))]


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
            replacement=model.replacement,
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
    ticket = await facade.query(task_id, actor)
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
    ticket = await facade.cancel(task_id, actor)
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
    ticket = await facade.retry(task_id, actor, confirm_charged=confirm_charged)
    return ApiResponse.ok(_task_out(ticket))


# --------------------------------------------------------------------------- #
# LibTV 账号面：额度 / 排队位次 / 消费明细
#
# 这三条只读接口把「浏览器里能看到、任务中心看不到」的状态搬到平台上：
# 任务卡只显示一个百分比，排队中和真在生成看起来一模一样；而「为什么排队」
# （`UNLIMITED_SERIAL` 的名额与位次）与「这笔到底扣没扣钱」（消费明细里的
# 上游 task_id）都在 LibTV 账号侧。接口细节见 `libtv/account.py`。
# --------------------------------------------------------------------------- #
def _benefit_out(item: dict[str, Any]) -> LibTvBenefitOut:
    return LibTvBenefitOut(
        model_version_id=item.get("modelVersionId"),
        target_name=item.get("targetName") or "",
        activity_name=item.get("activityName") or "",
        end_time=item.get("endTime") or "",
        tags=list(item.get("tags") or []),
    )


@router.get("/libtv/account", summary="LibTV 账号额度、免费权益与队列名额")
async def libtv_account(
    actor: DriverReadDep,
    account: Annotated[
        str, Query(description="CDP 地址，留空按计费模式挑（积分→9412，免费→第一个在线）")
    ] = "",
    _: None = Depends(rate_limit("model_driver.libtv_account", limit=30)),
) -> ApiResponse[LibTvAccountOut]:
    snapshot = await libtv_api.snapshot(account)
    asset = snapshot.get("asset")
    return ApiResponse.ok(
        LibTvAccountOut(
            account=snapshot["account"],
            asset=(
                LibTvAssetOut(
                    usable_power=asset.usable_power,
                    free_usable_power=asset.free_usable_power,
                    used_power=asset.used_power,
                    total_power=asset.total_power,
                    task_can_submit=asset.task_can_submit,
                    concurrent=asset.concurrent,
                    member_level=asset.name,
                    expire_date=asset.expire_date,
                )
                if asset is not None
                else None
            ),
            free_power=snapshot.get("freePower") or 0,
            free_power_expire_at=snapshot.get("freePowerExpireAt"),
            zero_power_benefits=[
                _benefit_out(item) for item in snapshot.get("zeroPowerBenefits") or []
            ],
        )
    )


@router.post("/libtv/progress", summary="批量查上游进度与排队位次（只读，不触发下载）")
async def libtv_progress(
    payload: LibTvProgressIn,
    actor: TaskReadDep,
    _: None = Depends(rate_limit("model_driver.libtv_progress", limit=60)),
) -> ApiResponse[LibTvProgressOut]:
    items = await libtv_api.progress(payload.task_ids, payload.account)
    return ApiResponse.ok(
        LibTvProgressOut(
            items=[
                LibTvQueueItemOut(
                    task_id=item.task_id,
                    status=item.status,
                    percent=item.percent,
                    queue_reason=item.queue_reason,
                    queue_position=item.queue_position,
                    queue_total=item.queue_total,
                    queue_limit=item.queue_limit,
                    power=item.power,
                    benefit_tag=item.benefit_tag,
                    remain_seconds=item.remain_seconds,
                    failed_reason=item.failed_reason,
                    describe=item.describe(),
                )
                for item in items
            ]
        )
    )


@router.get("/libtv/consumption", summary="积分消耗明细（含上游 task_id，对账用）")
async def libtv_consumption(
    actor: DriverReadDep,
    page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize", description="每页条数")] = 20,
    account: Annotated[str, Query(description="CDP 地址，留空按规则挑")] = "",
    _: None = Depends(rate_limit("model_driver.libtv_consumption", limit=20)),
) -> ApiResponse[LibTvConsumptionOut]:
    detail = await libtv_api.consumption(page, page_size, account)
    total = await libtv_api.consumption_total(account)
    return ApiResponse.ok(
        LibTvConsumptionOut(
            total=detail.total,
            page=detail.page,
            page_size=detail.page_size,
            total_count=total.count,
            total_amount=total.amount,
            items=[
                LibTvConsumptionItemOut(
                    amount=item.amount,
                    seconds=item.seconds,
                    source_name=item.source_name,
                    task_id=item.task_id,
                    project_name=item.project_name,
                    model_name=item.model_name,
                    summary=item.summary,
                )
                for item in detail.items
            ],
        )
    )
