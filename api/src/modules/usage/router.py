"""用量看板路由。

只有两个接口：

- `GET /usage/overview` —— 汇总 + 趋势 + 构成（一次请求拿齐）
- `GET /usage/records` —— 用量明细（分页）

为什么不是原来占位页上写的三个（`/usage/summary`、`/usage/trend`、`/usage/breakdown`）：
这三个问题问的是**同一个区间、同一个范围**，拆成三个接口就要跑三遍聚合，
还可能出现「三份数据来自三个不同时刻」的自相矛盾（顶部总数和下面的趋势对不上）。
合成一个之后，看板首屏只发一次请求。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.deps import require
from core.context import CurrentUser
from core.response import ApiResponse, Page, PageParams
from modules.usage.schema import (
    UsageGranularity,
    UsageOverviewOut,
    UsageRecordOut,
    UsageScope,
)
from modules.usage.service import UsageService

router = APIRouter(prefix="/usage", tags=["用量看板"])


def get_usage_service(
    actor: Annotated[CurrentUser, Depends(require("usage:read"))],
) -> UsageService:
    """看板的鉴权入口。

    鉴权做在依赖里而不是路由参数上，是为了让**服务层拿到的 actor 一定是被
    鉴权过的那个**：`UsageService` 内部要用 `actor.has("usage:read:all")`
    判断能不能看别人的用量，这个判断不能依赖调用方自觉传对。
    """
    return UsageService(actor)


UsageServiceDep = Annotated[UsageService, Depends(get_usage_service)]

StartQuery = Annotated[
    date | None,
    Query(description="区间起点（含，YYYY-MM-DD）。默认：结束日往前推 30 天"),
]
EndQuery = Annotated[
    date | None,
    Query(description="区间终点（含，YYYY-MM-DD）。默认：今天"),
]
GranularityQuery = Annotated[
    UsageGranularity,
    Query(description="时间桶粒度：day / week"),
]
ScopeQuery = Annotated[
    UsageScope,
    Query(description="数据范围：mine（自己）/ all（全部成员，需要 usage:read:all）"),
]


@router.get("/overview", summary="用量总览（汇总 / 趋势 / 构成）")
async def usage_overview(
    service: UsageServiceDep,
    start: StartQuery = None,
    end: EndQuery = None,
    granularity: GranularityQuery = UsageGranularity.DAY,
    scope: ScopeQuery = UsageScope.MINE,
) -> ApiResponse[UsageOverviewOut]:
    return ApiResponse.ok(
        await service.overview(start=start, end=end, granularity=granularity, scope=scope)
    )


@router.get("/records", summary="用量明细")
async def usage_records(
    service: UsageServiceDep,
    params: Annotated[PageParams, Depends()],
    start: StartQuery = None,
    end: EndQuery = None,
    scope: ScopeQuery = UsageScope.MINE,
) -> ApiResponse[Page[UsageRecordOut]]:
    return ApiResponse.ok(
        await service.records(start=start, end=end, scope=scope, params=params)
    )


__all__ = ["router"]
