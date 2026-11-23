"""LibTV 专属端点：额度 / 排队位次 / 消费明细。

## 为什么端点长在供应商侧

这三条只读接口把「浏览器里能看到、任务中心看不到」的状态搬到平台上：
任务卡只显示一个百分比，排队中和真在生成看起来一模一样；而「为什么排队」
（`UNLIMITED_SERIAL` 的名额与位次）与「这笔到底扣没扣钱」（消费明细里的
上游 task_id）都在 LibTV 账号侧。接口细节见 `libtv/account.py`。

框架的公共路由不认识 LibTV：端点由驱动文件 `drivers/libtv.py` 用
`EXTRA_ROUTES` 自报，registry 扫描时挂到模块 router 上——于是「加一个供应商」
不需要改公共文件一行。

## 前缀

子 router 自带 `/libtv` 前缀，registry 把它并进模块 router（`/model-driver`），
所以对外仍是 `/model-driver/libtv/...`。子 router **不带 tags**：标签由父
router 统一给，避免同一个接口在 OpenAPI 里出现两个重复的「模型驱动」。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from api.deps import rate_limit, require
from core.context import CurrentUser
from core.response import ApiResponse
from modules.model_driver.libtv import account as libtv_api
from modules.model_driver.libtv.schema import (
    LibTvAccountOut,
    LibTvAssetOut,
    LibTvBenefitOut,
    LibTvConsumptionItemOut,
    LibTvConsumptionOut,
    LibTvProgressIn,
    LibTvProgressOut,
    LibTvQueueItemOut,
)

router = APIRouter(prefix="/libtv")

DriverReadDep = Annotated[CurrentUser, Depends(require("model_driver:read"))]
#: 排队位次是**任务**的属性（用户看自己的任务为什么慢），不是驱动管理动作：
#: 用 `task:read` 而不是 `model_driver:read`，否则只有超管看得到 —— 而
#: `model_driver:read` 在种子角色里没有任何一个角色拥有。
TaskReadDep = Annotated[CurrentUser, Depends(require("task:read"))]


def _benefit_out(item: dict[str, Any]) -> LibTvBenefitOut:
    return LibTvBenefitOut(
        model_version_id=item.get("modelVersionId"),
        target_name=item.get("targetName") or "",
        activity_name=item.get("activityName") or "",
        end_time=item.get("endTime") or "",
        tags=list(item.get("tags") or []),
    )


@router.get("/account", summary="LibTV 账号额度、免费权益与队列名额")
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


@router.post("/progress", summary="批量查上游进度与排队位次（只读，不触发下载）")
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


@router.get("/consumption", summary="积分消耗明细（含上游 task_id，对账用）")
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
