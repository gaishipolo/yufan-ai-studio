"""视频任务的周期巡检：把「没有下一跳」的任务接回队列。

## 为什么必须有它

`image` 模块有 `image.reconcile`，video 没有。而 video 的链路长得多：
上传 → 提交 → 轮询 → 下载 → 落盘 → 入库。任何一环的 job 进死信
（poll job 被硬超时 nack、下载重试耗尽、执行器返回不可恢复码），
记录就停在 `running`：前端一直转圈，而**没有任何东西会再推进它**。

`generation.recovery` 的启动恢复只在**进程启动**时跑一次。用户不会为了一个
卡住的任务去重启服务，而 `QUEUE_DRIVER=memory` 时重启本身就是丢任务的来源。

## 只处理「很久没有写入」的记录

判据是 `updated_at`（任何一次落库都会更新它），不是「状态是 running」。
捞全部会让正在正常推进的任务也重新排一条 poll job：新 job 的 `polls=0`
把次数计数打回原点，「轮询次数上限」这条判据就被悄悄废掉了。
（2026-11-19 的排查里，正是这条判据让人误以为是次数用尽。）

## 误判的代价

阈值内（默认 900 秒）没有任何写入，正常视频早该出片了，所以误判概率很低。
即使误判，最坏也只是多一条 poll job —— 它与原链共享同一个幂等键，
不会重复提交、也不会重复计费；`handle_submit` 的三态守卫还会拦住任何
「已提交 / 结果未知」的重提。
"""

from __future__ import annotations

from core.config import settings
from core.logging import get_logger
from core.scheduler import periodic
from generation.recovery import recover_kind
from modules.task.pipeline import KIND_VIDEO

logger = get_logger(__name__)

#: 巡检的最小间隔（秒）。
#:
#: `@periodic` 只校验 `every >= 1`，而运维把 `TASK_RECONCILE_INTERVAL_SEC` 写成
#: 0.5 会导致**模块导入期直接抛 ValueError**（服务起不来）；写成 1 秒又会变成
#: 每秒一次全表扫描，把「延迟投递省下来的开销」一次用光。30 秒是这条扫描的下限。
_MIN_INTERVAL_SEC = 30.0
_RECONCILE_EVERY = max(_MIN_INTERVAL_SEC, float(settings.task_reconcile_interval_sec))


@periodic("task.reconcile", every=_RECONCILE_EVERY)
async def reconcile_stale_tasks() -> None:
    """把长时间没有推进的视频任务接回轮询 / 重新入队。"""
    if not settings.task_reconcile_enabled:
        return
    stale_after = int(settings.task_reconcile_stale_sec)
    if stale_after <= 0:
        # 关掉巡检的显式写法：0 或负数都不能被解释成「立刻全部重排」。
        return

    try:
        counts = await recover_kind(
            KIND_VIDEO,
            stale_after_sec=stale_after,
            limit=settings.task_reconcile_batch,
        )
    except Exception:  # noqa: BLE001 - 单轮失败不该让调度器记一次「handler 崩了」
        logger.exception("视频任务巡检失败（下一轮继续）")
        return

    count = sum(counts.values())
    if count:
        logger.info(
            "巡检接回 %d 个未完成的视频任务（超过 %d 秒没有写入）：%s",
            count,
            stale_after,
            counts,
        )
