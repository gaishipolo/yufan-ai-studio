"""用量看板的出入参契约。

## 口径（前端拿到的每个数字是什么意思）

- **尝试次数 `total`**：区间内发起过的生成任务数。失败与进行中也算。
- **已产出 `quantity`**：真正拿到手的数量（视频条数 / 图片张数）。失败的不算。
- **已产出时长 `durationSec`**：成功视频的时长之和（秒）。失败的不算。
- **成功率 `successRate`**：`succeeded / total`；`total` 为 0 时是 0。

这套口径与 `core.ports.usage_metrics` 里的约定一致，来源方（视频 / 生图）
各自按同一套规则算，看板只做相加。

## 为什么分成 `summary` / `trend` / `breakdown` 三块

三块回答的是三个不同的问题，粒度和维度都不一样，硬塞进一张表会逼着前端
在后端已经算好的数据里重新聚合 —— 那样后端的统计逻辑就会有两份实现。

## 所有字段都是必填

这里的数字**永远**有一个值（没有数据就是 0，不是 null）。所以 schema 里刻意
不给默认值 —— 有默认值会让 openapi 生成器把它们标成可选，前端就得到处写
`?? 0`，而那些 `?? 0` 里总有一天会漏掉一个，那一格就会显示 `undefined`。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from core.schema import CamelModel, UtcDateTime


class UsageScope(StrEnum):
    """数据范围。"""

    MINE = "mine"
    ALL = "all"


class UsageGranularity(StrEnum):
    """时间桶粒度。"""

    DAY = "day"
    WEEK = "week"


class UsageDimension(StrEnum):
    """构成维度。"""

    MODEL = "model"
    KIND = "kind"
    OWNER = "owner"
    STATUS = "status"


class UsageSourceOut(CamelModel):
    """一个来源（视频 / 生图 / 将来接上的音频…）的分项。"""

    source: str = Field(description="来源标识")
    total: int = Field(description="尝试次数")
    succeeded: int = Field(description="已产出次数")
    failed: int = Field(description="失败次数")
    pending: int = Field(description="排队 / 进行中")
    quantity: int = Field(description="已产出数量")
    duration_sec: int = Field(description="已产出视频总时长（秒）")


class UsageSummaryOut(CamelModel):
    """区间汇总。"""

    start: UtcDateTime = Field(description="区间起点（含）")
    end: UtcDateTime = Field(description="区间终点（含）")
    granularity: UsageGranularity = Field(description="实际使用的时间桶粒度")
    scope: UsageScope = Field(description="**实际生效**的数据范围（越权请求会被降级为 mine）")
    total: int = Field(description="尝试次数")
    succeeded: int = Field(description="已产出次数")
    failed: int = Field(description="失败次数")
    pending: int = Field(description="排队 / 进行中")
    success_rate: float = Field(description="成功率，0-1")
    quantity: int = Field(description="已产出数量合计")
    duration_sec: int = Field(description="已产出视频总时长（秒）")
    avg_duration_sec: float = Field(description="平均单条视频时长（秒）")
    sources: list[UsageSourceOut] = Field(description="各来源分项")


class UsageTrendPointOut(CamelModel):
    """趋势上的一个点（区间内没有数据的那天也会补一个 0）。"""

    bucket: str = Field(description="时间桶键：day=YYYY-MM-DD，week=该周周一")
    total: int = Field(description="尝试次数")
    succeeded: int = Field(description="已产出次数")
    failed: int = Field(description="失败次数")
    pending: int = Field(description="排队 / 进行中")
    quantity: int = Field(description="已产出数量")
    duration_sec: int = Field(description="已产出视频总时长（秒）")


class UsageBreakdownItemOut(CamelModel):
    """构成表的一行。"""

    dimension: UsageDimension = Field(description="维度")
    key: str = Field(description="维度内的稳定键")
    label: str = Field(description="展示名")
    total: int = Field(description="尝试次数")
    quantity: int = Field(description="已产出数量")
    duration_sec: int = Field(description="已产出视频总时长（秒）")
    share: float = Field(description="占全部尝试次数的比例，0-1")


class UsageOverviewOut(CamelModel):
    """看板总览：一次请求拿齐汇总 / 趋势 / 构成。"""

    summary: UsageSummaryOut = Field(description="区间汇总")
    trend: list[UsageTrendPointOut] = Field(description="时间趋势")
    breakdown: list[UsageBreakdownItemOut] = Field(description="构成行")
    sources: list[str] = Field(description="实际接上的数据来源标识")


class UsageRecordOut(CamelModel):
    """一条用量明细。"""

    id: str = Field(description="记录 ID")
    source: str = Field(description="来源标识")
    title: str = Field(description="展示标题")
    status: str = Field(description="原始状态")
    model_id: str = Field(description="模型目录 id")
    model_name: str = Field(description="模型可读名")
    quantity: int = Field(description="本次产出数量")
    duration_sec: int = Field(description="视频时长（秒）")
    created_at: UtcDateTime = Field(description="创建时间（UTC）")
    owner_id: str = Field(description="使用人 ID")
    owner_name: str = Field(description="使用人可读名")
    detail_url: str = Field(description="详情页深链接")


__all__ = [
    "UsageBreakdownItemOut",
    "UsageDimension",
    "UsageGranularity",
    "UsageOverviewOut",
    "UsageRecordOut",
    "UsageScope",
    "UsageSourceOut",
    "UsageSummaryOut",
    "UsageTrendPointOut",
]
