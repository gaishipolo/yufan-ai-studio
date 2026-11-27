"""任务模块的枚举。

**没有状态机表**：流转规则由写入口自己守（`modules/task/pipeline.py` 的
写操作都走 `db.guarded_update.update_unless_terminal`，谓词是「状态不在终态」——
终态之后一律不再接受写入）。曾经这里有一张
`_ALLOWED_TRANSITIONS` 和 `can_transition()`，全仓零调用点，而模块文档写着
「状态流转集中在这里」，等于给下一个人一个永远不会被执行的承诺
（2026-11-15 审查 P3）。真要做显式状态机，就把写入口全部改成只经它一个函数。
画面比例属于跨模块共享词汇，统一放在 `core.enums`。
"""

from __future__ import annotations

from enum import StrEnum

from core.enums import AspectRatio

__all__ = [
    "AspectRatio",
    "STATUS_LABEL",
    "TaskScope",
    "TaskSort",
    "TaskStatus",
    "TaskStatusFilter",
    "is_in_flight",
]


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatusFilter(StrEnum):
    """列表筛选值：`all` 与具体状态并列，避免路由层出现联合类型。"""

    ALL = "all"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskSort(StrEnum):
    CREATED_DESC = "createdAt_desc"
    CREATED_ASC = "createdAt_asc"
    TITLE_ASC = "title_asc"


class TaskScope(StrEnum):
    MINE = "mine"
    TEAM = "team"
    ALL = "all"


def is_in_flight(status: TaskStatus | str) -> bool:
    """是否存在未结束的任务 —— 前端据此决定是否继续轮询。"""
    return status in (TaskStatus.QUEUED, TaskStatus.RUNNING)


STATUS_LABEL: dict[TaskStatus, str] = {
    TaskStatus.QUEUED: "排队中",
    TaskStatus.RUNNING: "生成中",
    TaskStatus.COMPLETED: "已完成",
    TaskStatus.FAILED: "生成失败",
}
