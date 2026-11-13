"""跨生成链路的只读任务视图，不改变原任务状态或存储。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from core.ports.generation_feed import FeedGroup, ImageFeedItem
from core.response import Page
from core.schema import CamelModel, UtcDateTime
from modules.task.schema import TaskOut

FeedSource = Literal["all", "video", "image", "replacement"]
FeedStatus = FeedGroup


class TaskFeedItem(CamelModel):
    key: str = Field(description="带来源前缀的唯一键，不是上游任务 ID")
    id: str = Field(description="原始任务、生图记录或替换档案 ID")
    source: Literal["video", "image", "replacement"] = Field(description="任务来源")
    title: str = Field(description="展示标题")
    status: str = Field(description="原始状态，保留各链路的精确状态")
    group: FeedStatus = Field(description="统一筛选分组；running 包含排队")
    created_at: UtcDateTime = Field(description="创建时间")
    progress: int = Field(default=0, description="生成进度")
    source_name: str = Field(default="", description="替换原片名称")
    output_url: str | None = Field(
        default=None, description="成片 / 成图地址，三路都已按当前存储驱动现签；没有产物时为 null"
    )
    error_message: str | None = Field(default=None, description="失败或需处理原因")
    detail_url: str | None = Field(default=None, description="来源页面的深链接")
    video: TaskOut | None = Field(
        default=None, description="通用视频任务数据；其他来源为 null"
    )
    image: ImageFeedItem | None = Field(
        default=None, description="生图任务数据；其他来源为 null"
    )


class TaskFeedStats(CamelModel):
    all: int = Field(default=0, description="总数")
    running: int = Field(default=0, description="正在执行，包含排队")
    completed: int = Field(default=0, description="生成完成，不代表人工验收通过")
    failed: int = Field(default=0, description="执行失败")
    attention: int = Field(default=0, description="等待核对、免费额度、登录或人工处理")


class TaskFeedOut(Page[TaskFeedItem]):
    stats: TaskFeedStats = Field(default_factory=TaskFeedStats, description="筛选前分组统计")
    can_read_all: bool = Field(default=False, description="可否查看他人的通用任务")
    warnings: list[str] = Field(default_factory=list, description="部分来源不可用时的提示")
