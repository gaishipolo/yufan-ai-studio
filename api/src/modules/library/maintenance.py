"""回收站 GC：把过期条目物理删除。

## 为什么是小时级扫描而不是「每日 03:00」

调度器（`core/scheduler.py`）只有间隔，没有 cron 表达式。设计文档里写的是
「每日 03:00」，实现上取 3600 秒扫一次：保留期是 30 天，早一小时晚一小时对
「剩余 N 天」没有影响；而真按 24 小时跑，进程在那一刻重启就会漏掉整整一天。
需要精确时刻的话应该换调度器，不该在一个周期任务里手写「现在几点」。

## 三条纪律

1. **默认关闭**（`LIBRARY_TRASH_GC_ENABLED=false`）：删除不可逆，而
   `LEADER_ELECTOR=local` 时每个实例都认为自己该跑。单机确认后再开。
2. **被引用就跳过，不阻断**：一条被模板引用的资产不该让整轮 GC 停摆。
   跳过的条数进报告与审计，doctor 里也单独计数。
3. **只扫 `state='trash' AND trash_expires_at <= now`**：不做「没到期就顺手删」
   的优化——保留期是给用户反悔用的。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select

from core.audit import record as audit
from core.config import settings
from core.logging import get_logger
from core.scheduler import periodic
from db.session import get_session_factory
from infra import get_storage
from modules.library.lifecycle import LifecycleService
from modules.library.models import LibraryAsset

logger = get_logger(__name__)

__all__ = ["TrashGcReport", "run_trash_gc", "trash_gc"]


@dataclass
class TrashGcReport:
    scanned: int = 0
    purged: int = 0
    blocked: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def blocked_count(self) -> int:
        return len(self.blocked)


async def run_trash_gc(
    *, batch: int | None = None, dry_run: bool = False, now: datetime | None = None
) -> TrashGcReport:
    """扫一轮过期的回收站条目。`dry_run` 只列 id，不删任何东西（CLI 用）。"""
    limit = batch or settings.library_trash_gc_batch
    moment = now or datetime.now(UTC)
    report = TrashGcReport(dry_run=dry_run)
    async with get_session_factory()() as session:
        asset_ids = list(
            await session.scalars(
                select(LibraryAsset.id)
                .where(
                    LibraryAsset.state == "trash",
                    LibraryAsset.trash_expires_at.is_not(None),
                    LibraryAsset.trash_expires_at <= moment,
                )
                .order_by(LibraryAsset.trash_expires_at)
                .limit(limit)
            )
        )
        report.scanned = len(asset_ids)
        if dry_run:
            return report
        service = LifecycleService(session=session, storage=get_storage())
        for asset_id in asset_ids:
            purged = await service.purge_expired(asset_id)
            if purged:
                report.purged += 1
            else:
                # 被引用 / 已被并发恢复：跳过但记下来，别让它永远卡在这里没人知道
                report.blocked.append(asset_id)
    if report.purged or report.blocked:
        await audit(
            "library.trash.gc",
            target_type="library_asset",
            detail={
                "scanned": report.scanned,
                "purged": report.purged,
                "blocked": report.blocked_count,
            },
        )
        logger.info(
            "回收站 GC：扫描 %s，清理 %s，跳过 %s",
            report.scanned,
            report.purged,
            report.blocked_count,
        )
    return report


@periodic(
    "library.trash.gc",
    every=max(1.0, settings.library_trash_gc_interval_sec),
    immediate=False,
    # 删除不可逆：先抢集群租约，抢不到就跳过这一轮
    leader_only=True,
)
async def trash_gc() -> None:
    if not settings.library_trash_gc_enabled:
        logger.debug("回收站 GC 未启用（LIBRARY_TRASH_GC_ENABLED=false），本轮跳过")
        return
    await run_trash_gc()
