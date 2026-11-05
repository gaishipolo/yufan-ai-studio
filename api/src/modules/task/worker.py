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
from db.session import get_session_factory
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
        unknown: list[str] = []

        for task in rows:
            task.status = TaskStatus.QUEUED.value
            if task.external_id:
                # 供应商侧任务已在执行：保留 external_id 与进度，只把轮询接回来
                resume_poll.append(task.id)
            elif task.submit_attempts > 0:
                # 崩在「供应商已受理 → 写库」之间：无法判断有没有计费，
                # 自动重提就会产生第二份账单。宁可失败等人核对。
                unknown.append(task.id)
                task.status = TaskStatus.FAILED.value
                task.error_message = SUBMIT_UNKNOWN_MESSAGE
                task.finished_at = datetime.now(tz=UTC)
            else:
                # 提交前就中断了：重置现场，重新走 submit
                task.progress = 0
                task.error_message = None
                task.started_at = None
                resubmit.append(task.id)

        # 先提交再派发，避免 worker 抢到任务时读不到最新状态
        await session.commit()

    for task_id in unknown:
        logger.warning("任务提交结果未知，已置失败等待人工核对 id=%s", task_id)

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
