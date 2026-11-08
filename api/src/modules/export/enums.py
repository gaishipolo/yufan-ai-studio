"""导出状态与数据源枚举。

状态只有一个方向的合法迁移：

    queued ──> running ──> succeeded ──> expired
                   └────> failed

`expired` 不是失败：它表示**产物已被回收**（到期或手动清理），但记录行还在。
把它并进 `failed` 会让「当时到底生成成功没有」永久丢失，用户看到「失败」
会以为是系统的问题，于是反复重导同一份数据。
"""

from __future__ import annotations

from enum import StrEnum


class ExportStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


class ExportSource(StrEnum):
    """可导出的数据源白名单。

    只列已经在表里落了「谁、什么时候、做了什么」的数据。加新数据源要同时
    回答两件事：**谁能导出**（权限）、**导出时按什么过滤**（params 的必填项），
    见 `parameters.py` 的校验表。
    """

    TASKS = "tasks"
    AUDIT_EVENTS = "audit_events"


# 进行中的状态：同一用户对同一份「数据源 + 参数」不允许并发导出两份。
# 放在这里而不是 service 里，是因为回收任务也要判断「这条卡住了没有」。
ACTIVE_STATUSES: tuple[str, ...] = (ExportStatus.QUEUED.value, ExportStatus.RUNNING.value)
FINAL_STATUSES: tuple[str, ...] = (
    ExportStatus.SUCCEEDED.value,
    ExportStatus.EXPIRED.value,
    ExportStatus.FAILED.value,
)
