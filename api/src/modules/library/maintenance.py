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

import asyncio
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from sqlalchemy import select

from core.audit import record as audit
from core.config import settings
from core.logging import get_logger
from core.scheduler import periodic
from db.session import get_session_factory
from infra import get_storage
from infra.media_probe import create_preview
from infra.storage.base import ObjectStorage, StorageError
from modules.library.lifecycle import LifecycleService
from modules.library.models import LibraryAsset, LibraryPackItem, LibraryTag
from modules.library.service import preview_key
from modules.library.tags import sync_pack_tags

logger = get_logger(__name__)

__all__ = [
    "PackTagReport",
    "PreviewReport",
    "TAG_TREE_SEED",
    "TagTreeReport",
    "TrashGcReport",
    "run_pack_tag_sync",
    "run_preview_repair",
    "run_tag_tree_init",
    "run_trash_gc",
    "trash_gc",
]


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


@dataclass
class PreviewReport:
    """一轮封面修复的结果。`ok` 与 `rebuilt` 分开数，是因为「本来就是好的」和
    「这次修好了」都算「扫完没事」，但排查时只有后者能对上刚才看到的那张裂图。"""

    scanned: int = 0
    ok: int = 0
    rebuilt: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    dry_run: bool = False

    @property
    def broken(self) -> int:
        return len(self.failed)

    @property
    def pending(self) -> int:
        """需要重建的条数（试运行时就是「会修几条」）。"""
        return self.scanned - self.ok

    def lines(self) -> list[str]:
        head = (
            f"试运行：扫描 {self.scanned}，封面缺失或为空 {self.pending}"
            if self.dry_run
            else f"扫描 {self.scanned}，封面正常 {self.ok}，重建 {self.rebuilt}，失败 {self.broken}"
        )
        rows = [head]
        rows += [f"  失败 {asset_id}：{reason}" for asset_id, reason in self.failed[:20]]
        return rows


async def run_preview_repair(
    *, batch: int | None = None, dry_run: bool = False, owner: str = ""
) -> PreviewReport:
    """重建「记录在、字节不在」的缩略图。

    ## 为什么需要它

    原图按 `sha256` 落在 `library/blobs/`，封面是按同一摘要派生的
    `library/previews/*.jpg`：**两套字节，一条记录**。封面的字节没了，原图仍然完好，
    用户看到的却是列表一片渐变占位、详情页一个裂图标——这时候「素材丢了吗」
    只能靠翻存储才知道。对账不该靠人肉翻，所以给一个可反复跑的入口。

    ## 判据

    `cover_key` 非空但 `exists()` 为假，或者 `cover_key` 为空（当年生成失败，
    例如视频抽帧那次 ffmpeg 超时）——两者都要重建。`kind` 只扫 `image` / `video`：
    音频没有封面，封装的 `cover_key` 是**借**成员的（自己的 `sha256` 为空），
    给它重建会把别人的图写到 `previews/<空摘要>` 上去。

    `--dry-run` 只探测不写，用来先回答「到底有几条」。
    """
    storage = get_storage()
    report = PreviewReport(dry_run=dry_run)
    async with get_session_factory()() as session:
        query = (
            select(LibraryAsset)
            .where(
                LibraryAsset.deleted_at.is_(None),
                LibraryAsset.kind.in_(("image", "video")),
                LibraryAsset.storage_key != "",
                LibraryAsset.sha256 != "",
            )
            .order_by(LibraryAsset.created_at)
        )
        if owner:
            query = query.where(LibraryAsset.owner_id == owner)
        if batch:
            query = query.limit(batch)
        rows = (await session.scalars(query)).all()

        for row in rows:
            report.scanned += 1
            try:
                intact = bool(row.cover_key) and await storage.exists(row.cover_key)
            except StorageError:
                # 探测本身失败（权限、网关）：记成失败继续下一条，别让一条坏账号
                # 把整轮修复停住——这正是要来修「裂图」的人最不想看到的。
                report.failed.append((row.id, "封面探测失败"))
                continue
            if intact:
                report.ok += 1
                continue
            if dry_run:
                continue
            try:
                key = await _rebuild_cover(storage, row)
            except (StorageError, OSError, RuntimeError) as exc:
                report.failed.append((row.id, str(exc)[:120]))
                continue
            if not key:
                report.failed.append((row.id, "生成缩略图失败（原图可能已损坏）"))
                continue
            row.cover_key = key
            await session.commit()
            report.rebuilt += 1

    if report.rebuilt:
        logger.info(
            "封面修复：扫描 %s，重建 %s，失败 %s",
            report.scanned,
            report.rebuilt,
            report.broken,
        )
    return report


@dataclass
class PackTagReport:
    scanned: int = 0
    changed: list[str] = field(default_factory=list)
    added: int = 0
    dry_run: bool = False

    @property
    def pending(self) -> int:
        return len(self.changed)

    def lines(self) -> list[str]:
        rows = [
            f"试运行：扫描 {self.scanned} 个封装，{self.pending} 个缺成员标签（共 {self.added} 个）"
            if self.dry_run
            else f"扫描 {self.scanned} 个封装，补齐 {self.pending} 个（共 {self.added} 个标签）"
        ]
        if self.changed:
            rows.append("  涉及：" + ", ".join(self.changed[:20]))
        if self.dry_run and self.pending:
            rows.append("加 --no-dry-run 才写库")
        return rows


async def run_pack_tag_sync(
    *, batch: int = 0, dry_run: bool = False, owner: str = ""
) -> PackTagReport:
    """给**存量**封装补上成员标签并集。

    自动继承只在建封装 / 换成员时发生，所以本次改动之前建的封装仍然只有
    「封装」一个（外加用户手选的）标签。这条命令是可反复跑的回填：
    `sync_pack_tags` 只增不减，跑第二次 `pending` 应为 0，也不会覆盖用户
    后来在卡片上手动加的标签。

    默认试运行；`--no-dry-run` 才逐个 commit（一个封装失败不影响其它封装）。
    """
    report = PackTagReport(dry_run=dry_run)
    async with get_session_factory()() as session:
        query = (
            select(LibraryAsset.id)
            .where(
                LibraryAsset.kind == "pack",
                LibraryAsset.deleted_at.is_(None),
            )
            .order_by(LibraryAsset.created_at)
        )
        if owner:
            query = query.where(LibraryAsset.owner_id == owner)
        if batch:
            query = query.limit(batch)
        pack_ids = list((await session.scalars(query)).all())
        if not pack_ids:
            return report

        pairs = await session.execute(
            select(LibraryPackItem.pack_id, LibraryPackItem.asset_id).where(
                LibraryPackItem.pack_id.in_(pack_ids)
            )
        )
        members: dict[str, list[str]] = {}
        for pack_id, asset_id in pairs.all():
            members.setdefault(pack_id, []).append(asset_id)

        for pack_id in pack_ids:
            report.scanned += 1
            added = await sync_pack_tags(
                session, pack_id, members.get(pack_id, []), dry_run=dry_run
            )
            if not added:
                continue
            report.changed.append(pack_id)
            report.added += added
            if not dry_run:
                await session.commit()

    if report.added and not dry_run:
        logger.info(
            "封装标签回填：扫描 %s，补齐 %s 个封装 / %s 个标签",
            report.scanned,
            report.pending,
            report.added,
        )
    return report


# --------------------------------------------------------------------------- #
# 存量标签归层
# --------------------------------------------------------------------------- #
#: 用户拍板的初始映射：一级 → 旗下的二级。刻意**不写进迁移**：环境之间词表不同，
#: 一个新部署凭空多出几个标签比「没归层」更难解释。归层由运维命令按名字匹配。
TAG_TREE_SEED: dict[str, tuple[str, ...]] = {
    "产品": ("3030", "T35"),
    "角色": ("菜菜",),
}


@dataclass
class TagTreeReport:
    dry_run: bool = False
    changed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        verb = "试运行：将移动" if self.dry_run else "已移动"
        rows = [
            f"{verb} {len(self.changed)} 个标签到二级"
            + (f"：{', '.join(self.changed)}" if self.changed else "")
        ]
        if self.skipped:
            rows.append("跳过：" + "；".join(self.skipped[:20]))
        if self.dry_run and self.changed:
            rows.append("加 --no-dry-run 才写库")
        return rows


async def run_tag_tree_init(
    *, dry_run: bool = False, mapping: dict[str, tuple[str, ...]] | None = None
) -> TagTreeReport:
    """把存量扁平词表按 `TAG_TREE_SEED` 归成两级；幂等、默认试运行。

    只**提升**已有标签，不新建、不改名、不删绑定：迁移已经把全部存量标签设成
    一级，这里只把点名的几个改成二级，其余保持一级（包括「音乐」「封装」
    「AI生成」这些没有子标签的词——它们仍是能直接打在素材上的叶子）。

    跳过比"抢过来"安全：目标已挂在别的一级下、或它自己还有子标签（挂过去就
    成了三级），都记进报告让人去看，不悄悄改结构。第二次跑 `changed` 应为 0。
    """
    seed = TAG_TREE_SEED if mapping is None else mapping
    report = TagTreeReport(dry_run=dry_run)
    async with get_session_factory()() as session:
        rows = (await session.scalars(select(LibraryTag))).all()
        # 当父只认一级：同名二级是合法的，别把某个系列下的「小红书」当成父。
        # 但按名字找**待移动**的标签要包含二级，否则复跑会对已归层的三个标签
        # 报「不存在」——幂等命令不该把「已经好了」说成「缺东西」。
        top: dict[str, LibraryTag] = {}
        children_of: dict[str, list[str]] = {}
        for row in rows:
            if row.parent_id:
                children_of.setdefault(row.parent_id, []).append(row.name)
            else:
                top.setdefault(row.name, row)
        by_name: dict[str, LibraryTag] = dict(top)
        for row in rows:
            if row.parent_id:
                by_name.setdefault(row.name, row)

        for parent_name, child_names in seed.items():
            parent = top.get(parent_name)
            if parent is None:
                report.skipped.append(f"一级「{parent_name}」不存在")
                continue
            for child_name in child_names:
                child = by_name.get(child_name)
                if child is None:
                    report.skipped.append(f"「{child_name}」不存在")
                    continue
                if child.parent_id == parent.id:
                    continue  # 已就位，幂等
                if child.parent_id:
                    report.skipped.append(f"「{child_name}」已挂在别的一级下，不抢")
                    continue
                if children_of.get(child.id):
                    report.skipped.append(f"「{child_name}」还有子标签，挂过去会变成三级")
                    continue
                report.changed.append(f"{parent_name} / {child_name}")
                if not dry_run:
                    child.parent_id = parent.id
        if report.changed and not dry_run:
            await session.commit()
    return report


async def _rebuild_cover(storage: ObjectStorage, row: LibraryAsset) -> str:
    """下载原图 → 生成缩略图 → 写回对象存储。返回写成的 key，失败返回空串。"""
    with tempfile.TemporaryDirectory(prefix="library-cover-") as folder:
        source = Path(folder) / "source"
        local = storage.local_path(row.storage_key)
        if local is not None:
            if not local.is_file():
                raise StorageError(f"原图不存在：{row.storage_key}")
            source = local
        else:
            with source.open("wb") as handle:
                async for chunk in storage.iter_chunks(row.storage_key):
                    await asyncio.to_thread(handle.write, chunk)
        target = Path(folder) / "cover.jpg"
        if not await asyncio.to_thread(create_preview, source, target, row.kind):
            return ""
        if not target.is_file():
            return ""
        key = preview_key(cast(str, row.sha256))
        await storage.put_file(key, target, "image/jpeg")
        return key
