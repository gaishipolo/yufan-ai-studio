"""导出错误码（slot 9，见 `docs/EXTENDING.md` §7 的号段分配表）。

编号规则：`code = HTTP状态码 × 100 + 9 × 10 + seq`，
因此 `code // 100` 恒等于 HTTP 状态码。每个 (状态码, seq) 在一个模块内只能用一次，
冲突会在导入期由 `ModuleErrors` 直接报错，不会静默覆盖。
"""

from __future__ import annotations

from core.exceptions import AppError, ModuleErrors

errors = ModuleErrors("export", slot=9)


class ExportBusy(AppError):
    """同一个用户对同一份数据重复点了导出。

    返回 409 而不是静默复用上一条：用户点第二次通常是因为「没反应」，
    告诉他「正在导出」比让他等着更清楚。真正的去重靠入队的
    `idempotency_key`，它保证队列里不会有两份同样的活。
    """

    code = errors.code(409, 1)
    message = "同一份数据正在导出中，请等待它完成"
    http_status = 409


class ExportNotReady(AppError):
    """产物还不存在（排队中 / 生成中 / 已失败）。"""

    code = errors.code(409, 2)
    message = "导出尚未生成完成，请稍后再试"
    http_status = 409


class ExportGone(AppError):
    """记录还在，文件已被回收。

    刻意不用 404：404 的意思是「没有这条记录」，用户会以为记录丢了；
    410 表示「确实生成过、现在没了、重新导一次即可」。
    """

    code = errors.code(410, 1)
    message = "导出文件已过保留期被回收，请重新导出"
    http_status = 410
