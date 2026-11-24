"""工作区 GC：清理已经迁 DB 的终态运行目录。

## 为什么需要

`materialize()` 把 S3 素材下载一份到 `inputs/*`，工作区此前**没有清理机制**
（`cli.py` 只有 `runs-status`），磁盘只增不减（2026-11-23 核实）。

## 安全判据（四条全满足才删）

1. **终态**：run.json 的状态在终态集合里，正在跑的任务绝不碰
2. **已迁 DB**：`submission_receipts` 有这条 run 的回执 —— 没有就说明还没迁，
   删了工作区等于把唯一的提交凭证删掉（二次计费）
3. **超过保留期**：默认 30 天（与 `library_trash_retention_days` 同口径）
4. **无锁**：有锁文件的目录一律跳过（执行器可能还活着，只是暂时没写状态）

`run_id` 从 run.json / 回执读，**不用目录名** —— 工作区按日期分片
（`2026/11/06/<id>`），目录名不等于 run_id。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

from core.logging import get_logger
from core.scheduler import periodic
from infra.archive.runs import existing_runs, runs_root
from modules.model_driver.libtv.state import RUN_LOCK
from modules.model_driver.models import TERMINAL_STATUSES

logger = get_logger(__name__)

#: 工作区保留期（秒）。30 天 = 一个完整发布周期，与 library 回收站同口径。
WORKSPACE_RETENTION_SEC = 30 * 24 * 3600

#: 执行器真正在跑的标志（由 Node 桥接层创建/删除，见 libtv/process.py::executor_busy）。
#: `RUN_LOCK` 是监督进程独占的 OS 租约；`worker.lock` 是 Node 还活着的标志。
WORKER_LOCK = "records/worker.lock"

#: GC 每 6 小时跑一轮。不可逆操作用 leader_only，多实例只有一个实例动手。
GC_EVERY_SEC = 6 * 3600

#: 驱动刷新周期（秒）。
#:
#: 端点是**即时手段**，周期刷新是**收敛机制**：刷新是进程级的，多实例部署时
#: 端点只影响被打到的那个实例，其余实例要靠各自的周期刷新跟上。所以这里
#: `leader_only=False` —— 每个实例都跑。
#:
#: 间隔取 5 分钟：新增驱动文件通常伴随一次部署，不需要秒级。
REFRESH_EVERY_SEC = 300


def workspace_is_locked(folder: Path) -> bool:
    """按**真实锁位置**判断这个工作区还有没有执行器在跑。

    原来查 `folder.parent / ".locks" / f"{folder.name}.lock"` —— 那是日期分片
    目录，执行器根本不在那儿建锁。结果：真有任务在跑时 GC 照样把工作区删掉，
    而它永远不会因为一把不存在的锁而跳过。

    真实位置有两层：

    - `run/records/executor.lock`：监督进程的 OS 租约（见 `RUN_LOCK`）；
    - `run/records/worker.lock`：Node 桥接层的活动标志（见 `executor_busy`）；
    - `runs_root()/.locks/*.lock`：账号级串行锁。它不按 run 命名，但只要有
      一把，就说明**某个**执行器正在动工作区 —— GC 不可逆，宁可整体跳过。

    锁文件会残留（进程被 kill），所以这里只做「有没有」的文件判断，不猜它
    还活着没有；保守跳过一轮远好过删掉活任务的工作区。
    """
    if (folder / RUN_LOCK).exists() or (folder / WORKER_LOCK).exists():
        return True
    return any((runs_root() / ".locks").glob("*.lock"))


@periodic("model_driver.workspace.gc", every=GC_EVERY_SEC, immediate=False, leader_only=True)
async def gc_old_workspaces() -> None:
    """删掉「已迁 DB + 终态 + 超过保留期」的工作区目录。"""
    from sqlalchemy import select

    from db.session import get_session_factory
    from modules.model_driver.models import SubmissionReceipt

    cutoff = time.time() - WORKSPACE_RETENTION_SEC
    factory = get_session_factory()
    removed = 0
    skipped_locked = 0
    skipped_no_receipt = 0
    skipped_active = 0

    async with factory() as session:
        for folder in existing_runs():
            if workspace_is_locked(folder):
                skipped_locked += 1
                continue
            run_json = folder / "run.json"
            try:
                state = json.loads(run_json.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(state, dict):
                continue
            status = str(state.get("status") or "")
            if status not in TERMINAL_STATUSES:
                skipped_active += 1
                continue

            # 时间戳：优先 run.json 里的时间字段，没有就用文件 mtime 兜底
            changed = state.get("updated_at") or state.get("finished_at")
            try:
                if isinstance(changed, (int, float)):
                    age_basis = float(changed)
                else:
                    age_basis = run_json.stat().st_mtime
            except (OSError, TypeError, ValueError):
                continue
            if age_basis > cutoff:
                continue

            # run_id 从档案读，不用目录名（工作区按日期分片）
            run_id = str(state.get("run_id") or state.get("id") or "")
            if not run_id:
                skipped_no_receipt += 1
                continue
            result = await session.execute(
                select(SubmissionReceipt.id)
                .where(SubmissionReceipt.run_id == run_id)
                .limit(1)
            )
            if result.scalar_one_or_none() is None:
                # 没有 DB 回执：可能还没迁，工作区是唯一凭证，绝不删
                skipped_no_receipt += 1
                continue
            try:
                await asyncio.to_thread(shutil.rmtree, folder)
            except OSError:
                logger.exception("工作区 GC 删除失败，跳过：%s", folder)
                continue
            removed += 1

    if removed or skipped_no_receipt:
        logger.info(
            "工作区 GC：删除 %d，跳过（无 DB 回执）%d，跳过（锁）%d，跳过（非终态）%d",
            removed,
            skipped_no_receipt,
            skipped_locked,
            skipped_active,
        )


@periodic(
    "model_driver.drivers.refresh",
    every=REFRESH_EVERY_SEC,
    immediate=False,
    leader_only=False,
)
async def refresh_drivers_periodically() -> None:
    """周期性地把**新增**的驱动文件热接上。

    与端点是同一套逻辑（`registry.refresh()`），只是触发方式不同：
    端点给「我现在就要」用，周期给「多实例最终一致」用。

    失败只记日志、不抛：刷新失败不影响**已在跑的**驱动 —— 已经生效的东西
    不会因为一次刷新失败而回退。
    """
    from starlette.concurrency import run_in_threadpool

    from modules.model_driver.registry import refresh

    try:
        result = await run_in_threadpool(refresh)
    except Exception as exc:  # noqa: BLE001 - 周期刷新不该拖垮调度器
        logger.error("周期刷新模型驱动失败 %s", exc)
        return
    if result.added:
        logger.info("周期刷新已热接上新增驱动 %s", list(result.added))
    for problem in result.errors:
        logger.error("周期刷新模型驱动发现问题 %s", problem)
