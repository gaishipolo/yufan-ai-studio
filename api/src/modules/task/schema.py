"""任务模块的出入参定义。

与前端 `src/entities/task/model/types.ts` 一一对应（camelCase 由基类自动转换）。

字段的 `description` 不是装饰：`app export-openapi` 会把它写进 `docs/API.md`
的字段表，前端就是靠它理解 `downloadable` / `onlyAuthorized` 这类名字。
**新增字段时请一并写 description**，否则文档里那一列是空的。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from core.response import Page
from core.schema import CamelModel, UtcDateTime
from modules.task.enums import (
    AspectRatio,
    TaskScope,
    TaskSort,
    TaskStatus,
    TaskStatusFilter,
)


class TaskOut(CamelModel):
    """任务出参：字段名与前端 `VideoTask` 完全一致。"""

    id: str = Field(description="任务 ID")
    title: str = Field(description="标题")
    status: TaskStatus = Field(
        description="状态：queued 排队 / running 生成中 / completed 完成 / failed 失败"
    )
    model: str = Field(description="模型展示名，如 `Seedance 2.5`")
    # 目录 id：与 `GET /models` 的 `id` 可直接对齐（聚合、筛选、回到创建页都用它）
    model_id: str | None = Field(
        default=None, description="模型目录 id，与 `GET /api/v1/models` 的 `id` 对齐"
    )
    duration_sec: int = Field(description="视频时长（秒）")
    aspect_ratio: AspectRatio = Field(description="画幅比例")
    progress: int = Field(default=0, description="生成进度 0-100")
    cover_url: str = Field(default="", description="封面图 URL，未生成完时为空串")
    prompt: str = Field(default="", description="画面描述")
    style_preset: str | None = Field(default=None, description="风格预设标识，未设置时为 null")
    reference_library_ids: list[str] = Field(
        default_factory=list,
        description=(
            "参考图资产 id（资产库 v2），**按顺序生效**；图生视频用。"
            "只接受 `image` 类型、当前账号可读、未在回收站或草稿里的资产"
        ),
    )
    text_library_ids: list[str] = Field(
        default_factory=list, description="绑定的文本资产 id（顺序即 `{{Text N}}` 编号）"
    )
    text_bindings: list[dict[str, Any]] = Field(
        default_factory=list, description="绑定的文案与版本快照（不含正文）"
    )
    author_id: str = Field(description="作者用户 ID")
    author_name: str = Field(default="", description="作者昵称快照（改名不回填历史）")
    favorited: bool = Field(default=False, description="当前账号是否已收藏")
    downloadable: bool = Field(default=False, description="是否允许下载：已成功且有产物")
    error_message: str | None = Field(default=None, description="失败原因，成功时为 null")
    created_at: UtcDateTime = Field(description="创建时间（UTC）")
    updated_at: UtcDateTime = Field(description="更新时间（UTC）")
    started_at: UtcDateTime | None = Field(default=None, description="开始生成时间，未开始为 null")


class TaskStats(CamelModel):
    """各状态任务数：不受状态筛选影响，但受搜索/范围影响。"""

    all: int = Field(default=0, description="总数")
    queued: int = Field(default=0, description="排队中")
    running: int = Field(default=0, description="生成中")
    completed: int = Field(default=0, description="已完成")
    failed: int = Field(default=0, description="失败")


class TaskListOut(Page[TaskOut]):
    stats: TaskStats = Field(default_factory=TaskStats, description="侧边栏角标用的分组计数")
    # 当前账号是否有 `task:read:all`：前端据此收起「全部/团队」范围选项。
    # 没有它时后端会把 all/team 静默降级为「只看自己」，
    # 不透出这个标志，用户会以为「全部任务」里就是全站数据。
    can_read_all: bool = Field(
        default=False, description="当前账号能否查看他人任务；false 时前端应收起「全部」选项"
    )


class TaskListQuery(CamelModel):
    """列表筛选条件（service 入参，router 负责从 query string 组装）。"""

    status: TaskStatusFilter = Field(default=TaskStatusFilter.ALL, description="状态筛选")
    keyword: str = Field(default="", description="标题 / 模型 / ID 模糊搜索")
    sort: TaskSort = Field(default=TaskSort.CREATED_DESC, description="排序方式")
    scope: TaskScope = Field(
        default=TaskScope.MINE, description="数据范围：mine 我 / team 团队 / all 全站"
    )
    only_authorized: bool = Field(default=False, description="仅显示可下载（已成功）的任务")


class CreateTaskIn(CamelModel):
    """创建任务入参。

    `model` / `durationSec` 不设默认值：合法取值来自模型目录（`GET /models`），
    由 service 按供应商声明的 `paramSchema` 校验，写死默认值会在换模型时变成错值。
    两者都可以不传：不传模型取该能力的默认模型，不传时长取模型 schema 的默认档位。
    """

    prompt: str = Field(min_length=1, max_length=2000, description="画面描述")
    title: str | None = Field(default=None, max_length=200, description="不传则截取 prompt")
    model: str | None = Field(
        default=None, max_length=64, description="模型 id 或名称，缺省取默认模型"
    )
    duration_sec: int | None = Field(
        default=None, ge=1, le=600, description="视频时长（秒），缺省取模型的默认档位"
    )
    aspect_ratio: AspectRatio = Field(
        default=AspectRatio.PORTRAIT, description="画幅比例，缺省 9:16 竖屏"
    )
    reference_library_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "参考素材的资产库 v2 资产 id，最多 20 个，**顺序即提交顺序**。"
            "受理时校验：必须存在、当前账号能读、不在回收站 / 草稿里，且是"
            " `image` / `video` / `audio` 之一（能否收图、收几张由模型的"
            " `maxReferences` 决定）。传 id 而不是 URL：取件链接是私签的，供应商取不到"
        ),
    )
    text_library_ids: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "要拼进提示词的文本资产 id，按顺序对应 `{{Text 1}}` / `{{Text 2}}`…；"
            "提交时按当时版本冻结正文，之后编辑资产不影响本次生成与重试。"
            "只接受当前账号可读、在库的 text 类型资产，最多 8 份"
        ),
    )
    style_preset: str | None = Field(
        default=None, max_length=64, description="风格预设标识，留空为不套风格"
    )


class UpdateTaskIn(CamelModel):
    """局部更新（目前只开放展示属性，生成参数不可改，需重试）。"""

    title: str | None = Field(default=None, max_length=200, description="新标题")
    favorited: bool | None = Field(default=None, description="收藏状态")


class DeleteTasksIn(CamelModel):
    ids: list[str] = Field(min_length=1, max_length=200, description="要删除的任务 ID 列表")


class DeleteTasksOut(CamelModel):
    deleted: int = Field(description="实际删除的条数")
    skipped: list[str] = Field(
        default_factory=list,
        description="被跳过的任务 ID：不存在，或属于他人且没有 `task:read:all`",
    )


class TaskRetryOut(CamelModel):
    """重试的结果：只描述「这一次动作」。

    刻意**不**返回整份 `TaskOut`（R6-05）：`task:retry:any` 与 `task:read:all`
    是两个独立开关，客服只需要「帮别人重跑一次」，不该顺带获得
    「按 id 读任意失败任务的 prompt 与作者」这条读通道。
    重试是一个动作，不是一次查询——前端拿到结果后刷新列表即可。
    """

    id: str = Field(description="任务 ID")
    status: TaskStatus = Field(description="重试后的状态，正常为 queued")
    updated_at: UtcDateTime = Field(description="状态变更时间（UTC），即重新排队的时间")
