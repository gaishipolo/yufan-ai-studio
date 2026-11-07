"""归档垃圾回收：CLI 与周期任务共用同一份实现。

两处各写一遍一定会漂移，最危险的漂移是「一处查了引用、另一处没查」。
所以 `app prune-blobs` 和 `@periodic("asset.gc")` 都只调 `run_archive_gc()`。

默认**关闭**周期执行（`ARCHIVE_GC_ENABLED=false`）：调度器没有分布式选主，
多实例时每个进程都会跑一份；删除是不可逆的，开启前要先确认部署形态。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.logging import get_logger
from core.ports.assets import reference_source_gaps
from core.scheduler import periodic
from db.session import get_session_factory
from infra.archive import get_archive_storage
from modules.asset.archive_purge import purge_trash_asset
from modules.asset.errors import AssetInUse
from modules.asset.mirror import run_mirror_reconcile
from modules.asset.models import Asset
from modules.asset.schema import AssetSource

logger = get_logger(__name__)

__all__ = ["GcReport", "archive_gc", "run_archive_gc"]


@dataclass
class GcReport:
    orphans: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    protected: int = 0
    purged: list[tuple[str, str, list[str]]] = field(default_factory=list)
    blocked: list[tuple[str, str, list[str]]] = field(default_factory=list)

    @property
    def freed(self) -> int:
        return len(self.deleted) + sum(len(keys) for _, _, keys in self.purged)


async def _purge_old_trash(
    session: AsyncSession, *, execute: bool, trash_days: float
) -> tuple[list[tuple[str, str, list[str]]], list[tuple[str, str, list[str]]]]:
    # 不是「有任意一个 checker 就行」：漏登记第三个来源时照样会静默删掉被引用的素材。
    gaps = reference_source_gaps()
    if gaps:
        raise RuntimeError("引用检查不完备，拒绝清理回收站：" + "；".join(gaps))

    storage = get_archive_storage()
    cutoff = datetime.now(UTC) - timedelta(days=trash_days)
    purged: list[tuple[str, str, list[str]]] = []
    blocked: list[tuple[str, str, list[str]]] = []
    # 主要条件下推到 SQL；时间比较放应用层，只因为 SQLite 把带时区的
    # DateTime 存成 naive 值，直接比会得到「永不过期」。
    rows = (
        await session.execute(
            select(Asset).where(
                Asset.source == AssetSource.ARCHIVE,
                Asset.deleted_at.is_not(None),
            )
        )
    ).scalars().all()
    for row in rows:
        stamp = row.deleted_at
        if stamp is None:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        if stamp >= cutoff:
            continue
        try:
            keys = await purge_trash_asset(
                session,
                row,
                storage,
                action="asset.archive.gc",
                dry_run=not execute,
            )
        except AssetInUse as exc:
            refs = exc.details.get("references", []) if isinstance(exc.details, dict) else []
            blocked.append((row.id, row.name, list(refs)))
            continue
        purged.append((row.id, row.name, keys))
    return purged, blocked


async def run_archive_gc(
    *,
    execute: bool = False,
    min_age_hours: float = 24.0,
    trash_days: float = 30.0,
    include_trash: bool = False,
) -> GcReport:
    """报告（或执行）孤儿字节回收；`include_trash` 顺带清理超期的软删记录。

    dry-run 也会走引用检查，报告与 `--execute` 看到的必须是同一批。
    """
    report = GcReport()
    storage = get_archive_storage()
    async with get_session_factory()() as session:
        if include_trash:
            report.purged, report.blocked = await _purge_old_trash(
                session, execute=execute, trash_days=trash_days
            )

        rows = (await session.execute(select(Asset))).scalars().all()
        # 引用集合 = 库行的 storage_key/cover_key + 镜像 JSON 的同名字段。
        # 镜像必须算进来：模板 worker 直接读 `catalog/assets/*.json`。
        referenced: set[str] = set()
        for row in rows:
            referenced.update(key for key in (row.storage_key, row.cover_key) if key)
        for key in storage.list_keys("catalog/assets", suffix=".json"):
            value = storage.read(key)
            if not isinstance(value, dict):
                continue
            for field_name in ("path", "previewPath"):
                mirror_key = value.get(field_name)
                if isinstance(mirror_key, str) and mirror_key:
                    referenced.add(mirror_key)

    cutoff = datetime.now(UTC) - timedelta(hours=min_age_hours)
    for key in sorted(storage.list_keys("blobs")):
        if key in referenced:
            continue
        modified = storage.modified_at(key)
        if modified is None or modified > cutoff:
            report.protected += 1
            continue
        report.orphans.append(key)
        if execute:
            await asyncio.to_thread(storage.remove, key)
            report.deleted.append(key)
    return report


@periodic("asset.mirror", every=max(1.0, settings.archive_mirror_interval_sec), immediate=False)
async def mirror_reconcile() -> None:
    """按 DB 幂等重建镜像。不需要选主：多实例各写一份同样的内容，结果相同。

    这是「双写漂移可自动收敛」的那一半：业务侧只管提交 DB，投影失败或中途宕机
    留下的缺口，下一轮全部补齐；没有对应 DB 行的孤儿镜像也会被清掉。
    """
    if not settings.archive_mirror_enabled:
        return
    report = await run_mirror_reconcile()
    if report.failures:
        logger.warning("归档镜像对账有失败项：%s %s", report.summary(), report.failures[:5])
    elif report.written or report.removed:
        logger.info("归档镜像对账：%s", report.summary())


@periodic(
    "asset.gc",
    every=max(1.0, settings.archive_gc_interval_sec),
    immediate=False,
    leader_only=True,
)
async def archive_gc() -> None:
    """删除不可逆：除了 `ARCHIVE_GC_ENABLED=true`，还要抢到集群租约才执行。

    `leader_only` 让多实例部署只有一个进程真的删；单实例用默认 local 选主器即可。
    幂等的镜像对账不需要这层保护，故意没标。
    """
    if not settings.archive_gc_enabled:
        return
    report = await run_archive_gc(
        execute=True,
        min_age_hours=settings.archive_gc_min_age_hours,
        trash_days=settings.archive_gc_trash_days,
        include_trash=True,
    )
    logger.info(
        "归档 GC 完成：孤儿 %d（删除 %d，保护期 %d），回收站清理 %d、保留 %d",
        len(report.orphans),
        len(report.deleted),
        report.protected,
        len(report.purged),
        len(report.blocked),
    )
