"""任务模块的启动恢复逻辑。

生成本身已由 `generation` 框架接管（提交/轮询/重试/事件都在框架里），
本文件只保留一件框架不该关心的事：**进程重启后的现场恢复**。

内存队列重启即丢，`running` 状态的任务会永远卡住。启动时按「供应商侧是否已有任务」
分流，这一点直接关系到钱：
- 已有 `external_id`：只续轮询，不重复提交（否则每次重启都会重复扣费、产出两份视频）
- 无 `external_id`：说明提交前就中断了，重置现场后重新排队
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from core.logging import get_logger
from db.outbox import dispatch_pending, record
from db.session import get_session_factory
from generation.events import EVT_GENERATION_FAILED
from generation.runner import SUBMIT_UNKNOWN_MESSAGE, get_runner
from infra.queue.base import JobQueue
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask
from modules.task.pipeline import KIND_VIDEO

logger = get_logger(__name__)


async def resume_unfinished_tasks(queue: JobQueue) -> int:
    factory = get_session_factory()

    async with factory() as session:
        rows = (
            await session.scalars(
                select(VideoTask).where(
                    VideoTask.status.in_([TaskStatus.QUEUED.value, TaskStatus.RUNNING.value])
                )
            )
        ).all()
        if not rows:
            return 0

        # 三条路必须分开：已有 external_id 的只续轮询，否则会重复提交 / 重复计费；
        # 有提交意图但没有 external_id 的，连提都不能提（见下）
        resubmit: list[str] = []
        resume_poll: list[str] = []
        unknown: list[tuple[str, str]] = []

        for task in rows:
            if task.external_id:
                # 供应商侧任务已在执行：保留 external_id 与进度，只把轮询接回来。
                # 状态必须留在 running——否则前端会显示「排队中 · 62%」，
                # 要等下一次轮询才被拉回 running（R4-04）
                task.status = TaskStatus.RUNNING.value
                resume_poll.append(task.id)
            elif task.submit_attempts > 0:
                # 崩在「供应商已受理 → 写库」之间：无法判断有没有计费，
                # 自动重提就会产生第二份账单。宁可失败等人核对。
                unknown.append((task.id, task.author_id))
                task.status = TaskStatus.FAILED.value
                task.error_message = SUBMIT_UNKNOWN_MESSAGE
                task.finished_at = datetime.now(tz=UTC)
                # 事件与业务行写进**同一个事务**：以前是 commit 之后再 publish，
                # 中间崩一下就出现「任务页显示失败、事件流什么都没有」的静默消失（R4-10）。
                # 现在最坏情况是投递晚几秒，由 outbox 的周期任务补上。
                await record(
                    session,
                    EVT_GENERATION_FAILED,
                    kind=KIND_VIDEO,
                    record_id=task.id,
                    owner_id=task.author_id,
                    error=SUBMIT_UNKNOWN_MESSAGE,
                )
            else:
                # 提交前就中断了：重置现场，重新走 submit
                task.status = TaskStatus.QUEUED.value
                task.progress = 0
                task.error_message = None
                task.started_at = None
                resubmit.append(task.id)

        # 先提交再派发，避免 worker 抢到任务时读不到最新状态
        await session.commit()

    for task_id, _owner_id in unknown:
        logger.warning("任务提交结果未知，已置失败等待人工核对 id=%s", task_id)

    # 事务已提交，事件一定在 outbox 里。这里只做一次「尽快投递」的尝试，
    # 失败也不影响恢复流程——周期任务（core.outbox）会接着补投。
    if unknown:
        try:
            await dispatch_pending()
        except Exception:  # noqa: BLE001 - 投递失败不该让启动恢复中断
            logger.exception("启动恢复：outbox 立即投递失败，等待周期任务补投")

    runner = get_runner(queue)
    for task_id in resubmit:
        try:
            await runner.dispatch(KIND_VIDEO, task_id)
        except Exception:  # noqa: BLE001
            # 单条失败不阻塞其余任务恢复；任务仍是 queued，可再次重启或手动重试
            logger.exception("恢复任务失败（重新提交）id=%s", task_id)
    for task_id in resume_poll:
        try:
            await runner.resume(KIND_VIDEO, task_id)
        except Exception:  # noqa: BLE001
            logger.exception("恢复任务失败（续轮询）id=%s", task_id)

    logger.info(
        "恢复未完成任务 %d 个（重新提交 %d / 续轮询 %d / 结果未知 %d）",
        len(rows),
        len(resubmit),
        len(resume_poll),
        len(unknown),
    )
    return len(rows)
