"""用量看板的可插拔数据来源。

看板要回答的是「这段时间里生成了多少东西」，而生成能力天然分散在多个业务模块：
视频任务在 `modules/task`，生图作业在 `modules/image`。业务模块之间不许互相
import（`tests/test_architecture.py` 用 AST 守着这条线），所以看板不能自己去查
那两张表。

于是这里开一个**多实现**注入口：每种生成能力自己实现一份「用量读取器」，在
import 期注册进来；看板只认这份契约，把各来源的结果合并成一张图。两端互不认识。

## 为什么是多实现而不是单例

用量天然是「多路相加」。将来接上音频、数字人、数字替身，每一种都自带一份读取器，
看板自动纳入——**看板一行都不用改**。这就是这个注入口为将来留的扩展位：新增一种
生成能力 = 新增一个 provider 文件 + 一行注册，不动看板。

与 `core.ports.generation_feed`（单例）的区别不是风格问题：任务中心只需要「一份」
生图来源，看板需要「N 份」生成来源。基数不同，注入口形态就得不同。

## 未注册是合法状态

只跑单元测试、或某个能力还没接上时，这里可能是空的。看板此时返回**空看板**，
并在 `sources` 里如实列出接上了哪几路 —— 不报错，也不假装「用量为零」。

## 口径约定（看板与来源方必须一致）

- **区间**：`start` / `end` 都是闭区间（UTC）。来源方只做过滤，**不补零** ——
  缺哪一天由看板按 `granularity` 补齐，这样来源方不必关心粒度。
- **时长与数量只计已产出的**：失败的任务不计入视频时长、也不计入图片张数
  （否则用户会为没拿到的东西付账）。`total` 是尝试次数，与「已产出」分开。
- **数量口径**：视频按「条」计（一次生成一条）；生图按「张」计（`count`）。
- **时区**：一律 UTC。sqlite 读回的 datetime 是 naive 的，按 UTC 解释
  （与 `core.schema` 的出参序列化一致）。

## 共享的纯函数放这里

`aggregate_rows` / `bucket_key` / `classify_status` 几个函数所有来源方都要用，
而来源方之间不能互相 import —— 放在契约模块里是唯一不违反依赖方向的位置。
它们是纯函数，没有 I/O，放这里不会把 `core` 变成业务层。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from pydantic import Field

from core.ports.host import Port
from core.schema import CamelModel, UtcDateTime

#: 时间桶粒度。`day` 按自然日，`week` 按自然周（周一为起点）。
UsageGranularity = Literal["day", "week"]

#: 构成维度。
#:
#: - `model`：按模型（可读名优先，模型目录改名后历史数据也跟着变）
#: - `kind`：按生成方式（文生 / 图生，由参考素材推导）
#: - `owner`：按使用人
#: - `status`：按产出状态
UsageDimension = Literal["model", "kind", "owner", "status"]

#: 数据范围。`mine` 只看本人，`all` 看全部（看板侧已按权限收敛过）。
UsageScope = Literal["mine", "all"]

#: 记录归到哪个桶 —— 几个来源的状态词表不完全一样（视频用 `completed`，
#: 生图用 `succeeded`），所以在这里统一成一套。
UsageBucket = Literal["succeeded", "failed", "pending"]

#: 状态维度上的展示名。
_BUCKET_LABELS: dict[str, str] = {
    "succeeded": "已产出",
    "failed": "失败",
    "pending": "进行中",
}


class UsageQuery(CamelModel):
    """看板 → 来源方：已经按权限收敛过的查询条件。"""

    actor_id: str = Field(description="当前账号 ID")
    can_read_all: bool = Field(
        default=False, description="来源方自己的跨用户开关；false 时 scope 一律降级为只看本人"
    )
    scope: UsageScope = Field(default="mine", description="数据范围：mine / all")
    start: UtcDateTime = Field(description="区间起点（含）")
    end: UtcDateTime = Field(description="区间终点（含）")
    granularity: UsageGranularity = Field(default="day", description="时间桶粒度")
    limit: int = Field(default=0, ge=0, description="明细条数上限；0 = 不查明细")
    offset: int = Field(default=0, ge=0, description="明细偏移")


class UsageSourceSummary(CamelModel):
    """一份来源的汇总数字。"""

    source: str = Field(description="来源标识，如 video / image")
    total: int = Field(default=0, description="尝试次数（所有记录）")
    succeeded: int = Field(default=0, description="已产出次数")
    failed: int = Field(default=0, description="失败次数")
    pending: int = Field(default=0, description="排队 / 进行中")
    quantity: int = Field(default=0, description="已产出数量（视频条数 / 图片张数）")
    duration_sec: int = Field(default=0, description="已产出视频总时长（秒）")


class UsageTrendPoint(CamelModel):
    """时间桶上的一个点。"""

    bucket: str = Field(description="时间桶键：day=YYYY-MM-DD，week=该周周一")
    source: str = Field(description="来源标识")
    total: int = Field(default=0, description="尝试次数")
    succeeded: int = Field(default=0, description="已产出次数")
    failed: int = Field(default=0, description="失败次数")
    pending: int = Field(default=0, description="排队 / 进行中")
    quantity: int = Field(default=0, description="已产出数量")
    duration_sec: int = Field(default=0, description="已产出视频总时长（秒）")


class UsageBreakdownItem(CamelModel):
    """构成表的一行。"""

    dimension: UsageDimension = Field(description="维度")
    key: str = Field(description="维度内的稳定键")
    label: str = Field(description="展示名")
    source: str = Field(description="来源标识")
    total: int = Field(default=0, description="尝试次数")
    quantity: int = Field(default=0, description="已产出数量")
    duration_sec: int = Field(default=0, description="已产出视频总时长（秒）")


class UsageRecord(CamelModel):
    """一条用量明细。"""

    id: str = Field(description="记录 ID")
    source: str = Field(description="来源标识")
    title: str = Field(default="", description="展示标题")
    status: str = Field(default="", description="原始状态")
    model_id: str = Field(default="", description="模型目录 id")
    model_name: str = Field(default="", description="模型可读名")
    quantity: int = Field(default=1, description="本次产出数量（视频条数 / 图片张数）")
    duration_sec: int = Field(default=0, description="视频时长（秒）")
    created_at: UtcDateTime = Field(description="创建时间（UTC）")
    owner_id: str = Field(default="", description="使用人 ID")
    owner_name: str = Field(default="", description="使用人可读名；拿不到时留空")
    detail_url: str = Field(default="", description="详情页深链接")


class UsageRecordPage(CamelModel):
    """明细的一页。"""

    items: list[UsageRecord] = Field(default_factory=list, description="明细条目")
    total: int = Field(default=0, description="符合筛选条件的总条数")


class UsageRow(CamelModel):
    """来源方归一化后的一行。

    来源方把各自的表读成这个形状，剩下的聚合由 `aggregate_rows` 统一做 ——
    这样「视频时长只计已产出」这类口径只有一处实现，不会两路各写一遍然后跑偏。
    """

    created_at: datetime = Field(description="创建时间")
    status: str = Field(default="", description="原始状态词")
    model_id: str = Field(default="", description="模型目录 id")
    model_name: str = Field(default="", description="模型可读名")
    quantity: int = Field(default=1, description="本次产出数量")
    duration_sec: int = Field(default=0, description="视频时长（秒）")
    owner_id: str = Field(default="", description="使用人 ID")
    owner_name: str = Field(default="", description="使用人可读名")
    kind: str = Field(default="", description="生成方式（文生 / 图生…）")


class UsageAggregate(CamelModel):
    """一份来源的聚合结果：汇总 + 趋势 + 四个维度的构成。"""

    source: str = Field(description="来源标识")
    summary: UsageSourceSummary = Field(default_factory=UsageSourceSummary, description="汇总")
    trend: list[UsageTrendPoint] = Field(default_factory=list, description="时间趋势")
    breakdown: list[UsageBreakdownItem] = Field(default_factory=list, description="构成行")


class UsageMetricsProvider(Protocol):
    """一种生成能力的用量读取器。"""

    async def aggregate(self, query: UsageQuery) -> UsageAggregate:
        """把区间内的用量算成汇总 / 趋势 / 构成。"""

    async def records(self, query: UsageQuery) -> UsageRecordPage:
        """取明细。

        实现方应返回**前 `offset + limit` 条**（从第 0 条起），看板把各来源的
        结果合并排序后再切 `[offset, offset + limit)`。各来源排序键一致时这是
        精确的；实现方自己按 `offset` 翻页会让合并后的页码对不上。
        """


_providers: Port[UsageMetricsProvider] = Port("usage-metrics", multi=True)


def register_usage_metrics(key: str, provider: UsageMetricsProvider) -> None:
    """注册一份用量读取器。

    `key` 是稳定的字符串契约（如 `video` / `image`），会出现在看板的 `sources`
    与日志里。用字符串而不是类名：类名会随重构静默改名。
    """
    _providers.provide(provider, key=key, owner="core.ports.usage_metrics")


def reset_usage_metrics() -> None:
    """仅测试用：清空注册。"""
    _providers.reset()


def has_usage_metrics() -> bool:
    """`app modules` / `doctor` 据此显示装配状态。"""
    return _providers.has()


def usage_metrics_providers() -> list[tuple[str, UsageMetricsProvider]]:
    """当前接上的所有来源，按 key 排序（保证输出稳定）。"""
    return sorted(_providers.items().items())


# --------------------------------------------------------------------------- #
# 共享纯函数
# --------------------------------------------------------------------------- #
def as_utc(moment: datetime) -> datetime:
    """naive 一律按 UTC 解释。

    sqlite 读回的 datetime 是 naive 的，按本地时区解释会让东八区的时间往前挪
    8 小时，桶边界全错。
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def bucket_key(moment: datetime, granularity: UsageGranularity) -> str:
    """时间桶键。`week` 取该周的周一（ISO 周起点）。"""
    moment = as_utc(moment)
    if granularity == "week":
        moment = moment - timedelta(days=moment.weekday())
    return moment.strftime("%Y-%m-%d")


def classify_status(status: str) -> UsageBucket:
    """把各来源的状态词表收敛成三桶。

    视频用 `completed`、生图用 `succeeded`，词表不同但语义相同；未知状态一律
    归到 `pending`（宁可少算产出，也不能把没产出的东西算进去）。
    """
    normalized = (status or "").strip().lower()
    if normalized in {"succeeded", "completed", "success", "done"}:
        return "succeeded"
    if normalized in {"failed", "error", "cancelled", "canceled"}:
        return "failed"
    return "pending"


def aggregate_rows(
    source: str,
    rows: Iterable[UsageRow],
    granularity: UsageGranularity,
) -> UsageAggregate:
    """把归一化的行算成汇总 / 趋势 / 构成。

    口径都在这里，只有一份实现：

    - `total` 是**尝试次数**（所有行）；
    - `quantity`（已产出数量）与 `duration_sec`（已产出时长）**只计成功行**；
    - 趋势按 `granularity` 分桶，桶内不补零（补零由看板做）。
    """
    summary = UsageSourceSummary(source=source)
    trend: dict[str, UsageTrendPoint] = {}
    buckets: dict[tuple[str, str], UsageBreakdownItem] = {}

    def _slot(dimension: UsageDimension, key: str, label: str) -> UsageBreakdownItem:
        item = buckets.get((dimension, key))
        if item is None:
            item = UsageBreakdownItem(
                dimension=dimension, key=key, label=label, source=source
            )
            buckets[(dimension, key)] = item
        return item

    for row in rows:
        bucket = classify_status(row.status)
        summary.total += 1
        if bucket == "succeeded":
            summary.succeeded += 1
            summary.quantity += row.quantity
            summary.duration_sec += row.duration_sec
        elif bucket == "failed":
            summary.failed += 1
        else:
            summary.pending += 1

        key = bucket_key(row.created_at, granularity)
        point = trend.get(key)
        if point is None:
            point = UsageTrendPoint(bucket=key, source=source)
            trend[key] = point
        point.total += 1
        if bucket == "succeeded":
            point.succeeded += 1
            point.quantity += row.quantity
            point.duration_sec += row.duration_sec
        elif bucket == "failed":
            point.failed += 1
        else:
            point.pending += 1

        model_key = row.model_id or row.model_name or "unknown"
        model_label = row.model_name or row.model_id or "未知模型"
        for dimension, item_key, label in (
            ("model", model_key, model_label),
            ("kind", row.kind or "unknown", row.kind or "未知"),
            ("owner", row.owner_id or "unknown", row.owner_name or row.owner_id or "未知"),
            ("status", bucket, _BUCKET_LABELS.get(bucket, bucket)),
        ):
            item = _slot(dimension, item_key, label)
            item.total += 1
            if bucket == "succeeded":
                item.quantity += row.quantity
                item.duration_sec += row.duration_sec

    return UsageAggregate(
        source=source,
        summary=summary,
        trend=[trend[key] for key in sorted(trend)],
        breakdown=list(buckets.values()),
    )


__all__ = [
    "UsageAggregate",
    "UsageBucket",
    "UsageBreakdownItem",
    "UsageDimension",
    "UsageGranularity",
    "UsageMetricsProvider",
    "UsageQuery",
    "UsageRecord",
    "UsageRecordPage",
    "UsageRow",
    "UsageScope",
    "UsageSourceSummary",
    "UsageTrendPoint",
    "aggregate_rows",
    "as_utc",
    "bucket_key",
    "classify_status",
    "has_usage_metrics",
    "register_usage_metrics",
    "reset_usage_metrics",
    "usage_metrics_providers",
]
