"""任务中心的可插拔生成来源（当前实现是生图）。

业务模块之间不许互相 import：任务中心要显示 `image_jobs`，不能自己 import
`modules.image`，只能由生图模块在 import 期把一个只读 reader 注册到这里。
契约落在 `core`，两端都不认识对方。

读端是 async 且按 owner 全量返回、
由聚合方在内存里筛选；生图记录走数据库分页，必须把关键词 / 状态 / 排序 / 上限
一起传给来源方，否则每打开一次任务中心都要把整张表搬进内存。将来接第三种生成
能力时，也应该自带 reader，而不是让任务中心认识它的表。

未注册是合法状态（只跑任务模块的单测时就是如此）：返回空页，任务中心照常
显示另外两路，不抛异常、不假装「没有生图任务」。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import Field

from core.ports.host import Port
from core.schema import CamelModel, UtcDateTime

#: 与任务中心的状态页签同一套取值。`queued` 是筛选条件（不是分组），
#: 记录本身的 `group` 只会落在 running / completed / failed / attention。
FeedGroup = Literal["all", "queued", "running", "completed", "failed", "attention"]


class GenerationFeedQuery(CamelModel):
    """聚合方 → 来源方：已经按权限收敛过的查询条件。"""

    actor_id: str = Field(description="当前账号 ID")
    can_read_all: bool = Field(
        default=False, description="来源方自己的跨用户开关；false 时 scope 一律降级为只看本人"
    )
    scope: str = Field(default="mine", description="数据范围：mine / team / all")
    keyword: str = Field(default="", description="标题 / 提示词 / 模型 / ID 模糊搜索")
    status: FeedGroup = Field(default="all", description="状态页签")
    sort: str = Field(default="createdAt_desc", description="排序，与任务中心同一套取值")
    only_authorized: bool = Field(default=False, description="只看已有成图的记录")
    limit: int = Field(
        default=0,
        ge=0,
        description="最多返回多少条候选；0 = 只算统计不组装条目（statsOnly 用）",
    )


class ImageFeedItem(CamelModel):
    """一条生图记录的任务中心投影，字段够卡片与详情弹窗用即可。"""

    id: str = Field(description="生图记录 ID")
    title: str = Field(description="展示标题（记录名）")
    status: str = Field(description="原始状态：queued / running / succeeded / failed")
    group: FeedGroup = Field(description="统一筛选分组")
    created_at: UtcDateTime = Field(description="创建时间（UTC）")
    progress: int = Field(default=0, description="生成进度 0-100")
    model: str = Field(default="", description="模型可读名")
    model_id: str | None = Field(default=None, description="模型目录 id")
    size: str = Field(default="", description="尺寸，如 1024x1024")
    count: int = Field(default=1, description="一次生成的张数")
    prompt: str = Field(default="", description="画面描述")
    #: 提示词里的 `{{Text N}}` / `{{Mixed N}}` 要能就地换成正文与缩略图，所以把
    #: 两份 id 一起带出来：只给 prompt，前端只能看到占位符。
    reference_library_ids: list[str] = Field(
        default_factory=list, description="参考图资产 id，顺序即 `{{Mixed N}}`"
    )
    text_library_ids: list[str] = Field(
        default_factory=list, description="文本资产 id，顺序即 `{{Text N}}`"
    )
    cover_url: str = Field(
        default="", description="封面图地址（已按当前存储驱动现签，可直接用于 <img>）"
    )
    output_url: str | None = Field(
        default=None, description="成图地址（成功后才有），同样已现签"
    )
    error_message: str | None = Field(default=None, description="失败原因")
    external_id: str | None = Field(
        default=None, description="供应商任务号；有值时重试需先确认可能已扣费"
    )
    detail_url: str | None = Field(default=None, description="生图历史页深链接")


class GenerationFeedStats(CamelModel):
    """筛选前（但受关键词 / 范围影响）的分组计数，与 `TaskFeedStats` 同口径。"""

    all: int = Field(default=0, description="总数")
    running: int = Field(default=0, description="进行中，含排队")
    completed: int = Field(default=0, description="已完成且有成图")
    failed: int = Field(default=0, description="失败")
    attention: int = Field(default=0, description="已完成但没有成图，或状态未知")


class GenerationFeedPage(CamelModel):
    items: list[ImageFeedItem] = Field(
        default_factory=list, description="候选条目（最多 limit 条）"
    )
    stats: GenerationFeedStats = Field(
        default_factory=GenerationFeedStats, description="筛选前分组统计"
    )
    total: int = Field(default=0, description="按 status 筛选后的总数，供分页用")


GenerationFeedReader = Callable[[GenerationFeedQuery], Awaitable[GenerationFeedPage]]

_feed: Port[GenerationFeedReader] = Port("generation-feed", multi=False)


def register_generation_feed(reader: GenerationFeedReader) -> None:
    """注册口保持同步 + 注册语义：全局只有一个生图来源，重复注册直接报错。"""
    _feed.provide(reader, owner="modules.image")


def reset_generation_feed() -> None:
    """仅测试用：清空注册，验证「未注册时返回空页」这条约定。"""
    _feed.reset()


def has_generation_feed() -> bool:
    """`app modules` / `doctor` 据此显示装配状态。"""
    return _feed.has()


async def generation_feed(query: GenerationFeedQuery) -> GenerationFeedPage:
    """未注册时返回空页：可选依赖不能把任务中心拖垮。"""
    reader = _feed.single()
    return await reader(query) if reader else GenerationFeedPage()
