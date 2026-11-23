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

from core.logging import get_logger
from core.scheduler import periodic
from infra.archive.runs import existing_runs
from modules.model_driver.models import TERMINAL_STATUSES

logger = get_logger(__name__)

#: 工作区保留期（秒）。30 天 = 一个完整发布周期，与 library 回收站同口径。
WORKSPACE_RETENTION_SEC = 30 * 24 * 3600

#: GC 每 6 小时跑一轮。不可逆操作用 leader_only，多实例只有一个实例动手。
GC_EVERY_SEC = 6 * 3600


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
            if (folder.parent / ".locks" / f"{folder.name}.lock").exists():
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
