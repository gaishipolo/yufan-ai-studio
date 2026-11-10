"""替换任务的耗时口径：只在状态流转处写时间，别在别处补。

**耗时 = 「确认提交」到「这一轮停下」**，所以：

- 起点是 `queued`（用户点了确认/继续），不是 `created_at`：一条 `prepared` 的档案
  可以在列表里放几天再提交，创建时间回答不了「跑了多久」。
- 每次重新提交都**重开一轮**：失败后隔天重试的任务如果沿用第一次的起点，会显示成
  「耗时 20 小时」，而用户想知道的是这次跑了多久。上一轮的时间不保留——它的价值
  在调用档案里的回执，不在这一列。
- 终点是四种「停下」的状态：`success` / `failed` / `service_error` /
  `submission_unknown`。前三个好理解；`submission_unknown` 是「提交可能已扣费、
  必须人工核对」的停住状态，算结束，`reconfirm` 重提时再开一轮。
- `waiting_free` / `waiting_executor` 不是终点：它们在等额度/等执行器，还会继续走。

历史行两列都是 `NULL`（迁移不回填），页面显示「未记录」——不拿 `updated_at` 冒充，
它会被评价、回收站、补积分推着走。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from db.base import utcnow

if TYPE_CHECKING:  # pragma: no cover - 只为类型，运行时不需要导入模型
    from modules.template.models import ReplacementRun

# 进入这些状态即「结束这一轮」，允许带 `finished_at`
FINISHED_STATUSES = frozenset({"success", "failed", "service_error", "submission_unknown"})
# 提交环节的中间态：`queued` 开表，其余只在缺起点时兜底补一次
RUNNING_STATUSES = frozenset(
    {"queued", "uploading", "uploaded", "submitting", "running", "generated"}
)


def begin(run: ReplacementRun, *, now: datetime | None = None) -> None:
    """开一轮计时。重提时调用会覆盖上一轮的起止。"""
    run.started_at = now or utcnow()
    run.finished_at = None


def finish(run: ReplacementRun, *, now: datetime | None = None) -> None:
    """落终点；没有起点就不编一个终点（宁可显示「未记录」）。"""
    if run.started_at is None:
        return
    if run.finished_at is None:
        run.finished_at = now or utcnow()


def set_status(run: ReplacementRun, status: str, *, now: datetime | None = None) -> None:
    """改状态并顺带处理计时。所有「会开始/结束一轮」的赋值都走这里。"""
    run.status = status
    if status == "queued":
        begin(run, now=now)
    elif status in RUNNING_STATUSES:
        if run.started_at is None:
            begin(run, now=now)
    elif status in FINISHED_STATUSES:
        finish(run, now=now)


def elapsed_seconds(run: ReplacementRun, *, now: datetime | None = None) -> int | None:
    """已走秒数；未开始为 `None`。

    未完成时按「到此刻为止」算，页面上标成「已用」，刷新即更新。
    """
    if run.started_at is None:
        return None
    if run.finished_at is None:
        if run.status in FINISHED_STATUSES:
            # 终态却没有终点：说明这一行不是从 `set_status` 走过来的（旧数据、
            # 或执行器回执直接改的状态）。按「到此刻」算会得到一个一直涨的数字，
            # 比显示「未记录」更误导。
            return None
        return max(0, int((_aware(now or utcnow()) - _aware(run.started_at)).total_seconds()))
    return max(0, int((_aware(run.finished_at) - _aware(run.started_at)).total_seconds()))


def _aware(value: datetime) -> datetime:
    """sqlite 读回来的时间没有时区，按 UTC 补齐（与 `image.maintenance` 同一处理）。"""
    return value if value.tzinfo else value.replace(tzinfo=UTC)
