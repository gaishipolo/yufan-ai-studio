"""任务模块的枚举与状态机。

状态流转集中在 `can_transition`，避免各 service 各写一套判断。
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
    "can_transition",
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


# 合法流转：未结束的任务可以前进；失败/完成是终态（重试会重置回 queued）
_ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.FAILED},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
}


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in _ALLOWED_TRANSITIONS[current]


def is_in_flight(status: TaskStatus | str) -> bool:
    """是否存在未结束的任务 —— 前端据此决定是否继续轮询。"""
    return status in (TaskStatus.QUEUED, TaskStatus.RUNNING)


STATUS_LABEL: dict[TaskStatus, str] = {
    TaskStatus.QUEUED: "排队中",
    TaskStatus.RUNNING: "生成中",
    TaskStatus.COMPLETED: "已完成",
    TaskStatus.FAILED: "生成失败",
}
