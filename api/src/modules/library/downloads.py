"""批量下载：异步打包 + 流式取件（设计 §6）。

## 为什么不在请求线程里压 zip

`zipfile` 是同步阻塞的，几十上百 MB 的读取 + 写盘会把整个 worker 拖住；而这个
项目的请求线程本来就短命（每个请求一个协程），压包期间连 `/health/ready` 都要排队。
所以走「建任务 → 入队 → 后台压 → 轮询 → 取件」，与导出 / 模板替换同一个模型。

## 三条口径

1. **只打包 `state=active` 的条目**：草稿与回收站在语义上「还没有 / 已经不属于」
   库内内容，把它们打进包会让「删除」变成只删了个标签。逐条给原因，不整单失败。
2. **闸门在展开之后**：请求里最多 200 个 id（封装要展开），但真正限制的是
   **展开后的条目数（100）与解压后总量（2GB）**。按资产行登记的 `size_bytes` 求和，
   不按 zip 实际大小——媒体压缩率不可预测，用 zip 大小会让上限形同虚设。
3. **`ZIP_STORED` 而不是 `ZIP_DEFLATED`**：图片 / 视频 / 音频本身已经压过，
   deflate 只能再省几个百分点，却要在 2GB 上白烧一遍 CPU。

## 幂等与状态机

    queued ──(worker 领取)──> running ──(压完)──> succeeded ──(到期)──> expired
                                └──(异常)──> failed ──(到期)──> expired

`build_download` 只处理 `queued`：队列驱动可能重投（db 驱动在租约丢失后会），
第二遍必须什么都不做。`failed` 保留 `error` 供用户重试——重试是**新建一条任务**，
不是把旧任务改回 queued（否则历史里的失败次数就丢了）。
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import record as audit
from core.config import settings
from core.context import CurrentUser
from core.exceptions import NotFound
from core.logging import get_logger
from core.scheduler import periodic
from core.serving import Served
from db.session import get_session_factory
from infra import get_queue, get_storage
from infra.queue.base import Job
from infra.storage.base import ObjectStorage
from modules.library import access
from modules.library.errors import DownloadEmpty, DownloadNotReady, DownloadTooLarge
from modules.library.models import (
    LibraryAsset,
    LibraryDownloadJob,
    LibraryPackItem,
)
from modules.library.schema import DownloadIn, DownloadJobOut, DownloadSkip
from workers import register_handler

__all__ = [
    "DownloadGcReport",
    "DownloadService",
    "build_download",
    "enqueue_download",
    "run_download_gc",
]

logger = get_logger(__name__)

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
EXPIRED = "expired"

#: zip 落在独立前缀下。与 `library/blobs` 分开：blob 是内容寻址、被引用计数保护，
#: 而这里是我们自己的临时产物，过期就该删，不该混进「孤儿 blob」的清理逻辑。
DOWNLOAD_PREFIX = "library/downloads"

#: 卡在 queued/running 超过这个时长就判定为「任务丢了」。
#: worker 停机、队列驱动重建、进程被杀都会留下这样的行；不处理的话前端会
#: 永远转圈，用户既不能重试也不知道该等多久。
STUCK_AFTER = timedelta(hours=2)

#: 过期任务行的保留期：字节删掉但行留 7 天，用户点进来看到「已过期」而不是 404。
PURGE_AFTER = timedelta(days=7)

_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class DownloadService:
    def __init__(self, session: AsyncSession, storage: ObjectStorage) -> None:
        self.session = session
        self.storage = storage

    # ------------------------------------------------------------------ 发起

    async def create(self, payload: DownloadIn, actor: CurrentUser) -> DownloadJobOut:
        selected, skipped = await self._collect(payload.ids, actor)
        if not selected:
            raise DownloadEmpty()

        limit_items = settings.library_download_max_items
        if len(selected) > limit_items:
            raise DownloadTooLarge(
                details={
                    "unit": "items",
                    "limit": limit_items,
                    "actual": len(selected),
                }
            )
        total_bytes = sum(row.size_bytes for row in selected)
        limit_bytes = settings.library_download_max_bytes
        if total_bytes > limit_bytes:
            raise DownloadTooLarge(
                details={
                    "unit": "bytes",
                    "limit": limit_bytes,
                    "actual": total_bytes,
                }
            )

        job = LibraryDownloadJob(
            owner_id=actor.id,
            asset_ids=[row.id for row in selected],
            status=QUEUED,
        )
        self.session.add(job)
        await self.session.commit()
        try:
            await enqueue_download(job.id)
        except Exception:  # noqa: BLE001 - 队列不可用要落到任务行上，不能只报 500
            logger.exception("打包任务入队失败 id=%s", job.id)
            job.status = FAILED
            job.error = "打包队列暂不可用，请稍后重试"
            job.finished_at = datetime.now(UTC)
            job.expires_at = datetime.now(UTC) + timedelta(
                hours=settings.library_download_ttl_hours
            )
            await self.session.commit()
        return self._to_out(job, skipped=skipped)

    # ------------------------------------------------------------------ 读取

    async def get(self, job_id: str, actor: CurrentUser) -> DownloadJobOut:
        return self._to_out(await self._load(job_id, actor))

    async def list_jobs(self, actor: CurrentUser) -> list[DownloadJobOut]:
        """我最近的打包任务。只给最近 20 条：这是「刚才那个包好了没」的入口，
        不是下载历史（历史靠审计与对象存储的生命周期）。"""
        rows = (
            await self.session.scalars(
                select(LibraryDownloadJob)
                .where(LibraryDownloadJob.owner_id == actor.id)
                .order_by(LibraryDownloadJob.created_at.desc())
                .limit(20)
            )
        ).all()
        return [self._to_out(row) for row in rows]

    async def file(self, job_id: str, actor: CurrentUser) -> Served:
        row = await self._load(job_id, actor)
        if row.status != SUCCEEDED or not row.storage_key:
            raise DownloadNotReady(details={"status": row.status})
        expires_at = _as_utc(row.expires_at)
        if expires_at is not None and expires_at <= datetime.now(UTC):
            # 不在这里改状态：GET 不该有副作用，清扫交给周期任务
            raise DownloadNotReady(details={"status": EXPIRED})
        filename = f"library-{row.id}.zip"
        path = self.storage.local_path(row.storage_key)
        if path is not None:
            # 支持 Range：浏览器续传 / 断点重下都靠它
            return Served.local(path, "application/zip", filename=filename)
        # 远端**不能** 307：前端是 XHR 取 blob 再 `saveBlob` 的，跨域重定向要求
        # 对象存储回 CORS 头。MinIO 当前的 `PutBucketCors` 直接回 NotImplemented，
        # 这条链路就变成「控制台报 blocked by CORS、功能时好时坏」。
        # 同源转发还顺手修了文件名：307 时浏览器拿到的名字是 key 的最后一段，
        # 也就是一串 uuid，用户看到的是「474a98…zip」。
        return Served.stream(
            self.storage.iter_chunks(row.storage_key),
            "application/zip",
            filename=filename,
            size=row.size_bytes or None,
        )

    # ------------------------------------------------------------------ 内部

    async def _load(self, job_id: str, actor: CurrentUser) -> LibraryDownloadJob:
        row = await self.session.get(LibraryDownloadJob, job_id)
        # 别人的任务一律 404：既不说「存在但没权限」，也不泄露「这个人下过什么」
        if row is None or (row.owner_id != actor.id and not actor.has(access.PERM_DELETE_ANY)):
            raise NotFound("打包任务不存在")
        return row

    async def _collect(
        self, ids: list[str], actor: CurrentUser
    ) -> tuple[list[LibraryAsset], list[DownloadSkip]]:
        """逐个判定「能不能进包」，封装在这里展开。返回 (可打包, 跳过明细)。"""
        wanted = list(dict.fromkeys(ids))
        rows = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(LibraryAsset).where(LibraryAsset.id.in_(wanted))
                )
            ).all()
        }
        my_groups = await access.all_groups(self.session, actor)
        group_map = await access.asset_group_ids(self.session, wanted)
        # 「封装成员」这条读入口要查库，一次问完这一批，别在循环里按 id 打点
        via_pack = await access.readable_via_pack_ids(
            self.session, wanted, actor, my_groups
        )

        selected: list[LibraryAsset] = []
        skipped: list[DownloadSkip] = []
        pack_ids: list[str] = []
        for asset_id in wanted:
            row = rows.get(asset_id)
            if row is None or not (
                access.can_read(row, actor, my_groups, group_map.get(asset_id, []))
                or asset_id in via_pack
            ):
                # 预览能看、单条能下，批量却跳过，用户只会觉得「打包坏了」
                skipped.append(DownloadSkip(id=asset_id, reason="不存在或无权访问"))
                continue
            if row.kind == "pack":
                # 封装没有自己的字节，`_skip_reason` 最后那条「没有可下载的原文件」
                # 对它是常态，所以这里只判状态。少了这一步，**回收站里的封装仍能
                # 打包**：它的 `can_read` 对 owner 是 true，成员于是照常展开——
                # 「解散」也就等于没解散。
                state_reason = _state_skip_reason(row)
                if state_reason:
                    skipped.append(
                        DownloadSkip(id=row.id, name=row.name, reason=state_reason)
                    )
                    continue
                pack_ids.append(row.id)
                continue
            reason = _skip_reason(row)
            if reason:
                skipped.append(DownloadSkip(id=row.id, name=row.name, reason=reason))
                continue
            selected.append(row)

        if pack_ids:
            selected.extend(await self._expand_packs(pack_ids, actor, skipped, my_groups))
        return _dedupe(selected), skipped

    async def _expand_packs(
        self,
        pack_ids: list[str],
        actor: CurrentUser,
        skipped: list[DownloadSkip],
        my_groups: list[str],
    ) -> list[LibraryAsset]:
        """封装展开成成员。成员可能已经被删 / 被取消共享 —— 逐条跳过并说明。

        不递归：建封装时已经禁止嵌套（`PackService._validate`），这里再判断一次
        是防「历史数据里有一条嵌套」把下载变成潜在的死循环。

        成员的可读性用**同一个读入口**判（`can_read` 或「封装成员」）：调用方
        能走到这里，说明封装本身已通过读判定，而封装对成员的授权是
        `access.readable_via_pack_clause` 定义的。只按成员行自己的 `visibility`
        判，别人下载我共享出去的封装会拿到**空 zip**——比报错更难解释。
        """
        member_ids = list(
            await self.session.scalars(
                select(LibraryPackItem.asset_id)
                .where(LibraryPackItem.pack_id.in_(pack_ids))
                .order_by(LibraryPackItem.pack_id, LibraryPackItem.position)
            )
        )
        if not member_ids:
            for pack_id in pack_ids:
                skipped.append(DownloadSkip(id=pack_id, reason="封装里没有成员"))
            return []
        rows = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(LibraryAsset).where(LibraryAsset.id.in_(member_ids))
                )
            ).all()
        }
        group_map = await access.asset_group_ids(self.session, member_ids)
        via_pack = await access.readable_via_pack_ids(
            self.session, member_ids, actor, my_groups
        )
        expanded: list[LibraryAsset] = []
        for member_id in member_ids:
            row = rows.get(member_id)
            if row is None or not (
                access.can_read(row, actor, my_groups, group_map.get(member_id, []))
                or member_id in via_pack
            ):
                skipped.append(DownloadSkip(id=member_id, reason="封装成员已不可访问"))
                continue
            reason = _skip_reason(row)
            if reason:
                skipped.append(DownloadSkip(id=row.id, name=row.name, reason=reason))
                continue
            if row.kind == "pack":
                skipped.append(DownloadSkip(id=row.id, name=row.name, reason="不支持嵌套封装"))
                continue
            expanded.append(row)
        return expanded

    def _to_out(
        self, row: LibraryDownloadJob, *, skipped: list[DownloadSkip] | None = None
    ) -> DownloadJobOut:
        download_url = (
            f"{settings.api_prefix}/library/downloads/{row.id}/file"
            if row.status == SUCCEEDED and row.storage_key
            else None
        )
        return DownloadJobOut(
            id=row.id,
            status=row.status,
            asset_count=len(row.asset_ids or []),
            size_bytes=row.size_bytes,
            error=row.error,
            download_url=download_url,
            expires_at=row.expires_at,
            created_at=row.created_at,
            finished_at=row.finished_at,
            skipped=skipped or [],
        )


def _state_skip_reason(row: LibraryAsset) -> str:
    """只看状态的原因。空串 = 状态上可以进包。

    与 `_skip_reason` 拆开是因为封装要单独判：它天生没有 `storage_key`。
    """
    if row.state == "draft":
        return "草稿未入库"
    if row.state == "trash":
        return "回收站内容不可下载"
    return ""


def _skip_reason(row: LibraryAsset) -> str:
    """不能进包的原因。空串 = 可以打包。"""
    reason = _state_skip_reason(row)
    if reason:
        return reason
    if not row.storage_key:
        return "没有可下载的原文件"
    return ""


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 的 `DateTime(timezone=True)` 读回来不带 tzinfo。

    直接和 `datetime.now(UTC)` 比会抛 `TypeError`——这个错误只在「取件」这条
    路径上出现，测试里不点一下文件就发现不了（本次就是这么炸出来的）。
    全仓同一口径：naive 一律按 UTC 解释。
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _dedupe(rows: list[LibraryAsset]) -> list[LibraryAsset]:
    seen: set[str] = set()
    result: list[LibraryAsset] = []
    for row in rows:
        if row.id in seen:
            continue
        seen.add(row.id)
        result.append(row)
    return result


# --------------------------------------------------------------------------- #
# 后台构建
# --------------------------------------------------------------------------- #
def _safe_name(name: str, fallback: str) -> str:
    """去掉路径分隔符与控制字符，保留扩展名。

    压缩包里的名字就是用户解压后看到的文件名：带正斜杠会解出一层目录，
    带反斜杠在 Windows 上同样；截断时保扩展名，否则播放器认不出来。
    """
    cleaned = _UNSAFE_NAME.sub("_", name).strip().strip(".")
    if not cleaned:
        return fallback
    if len(cleaned) <= 120:
        return cleaned
    suffix = Path(cleaned).suffix[:12]
    return cleaned[: 120 - len(suffix)] + suffix


def _zip_entries(target: Path, entries: list[tuple[str, Path]]) -> None:
    """同步压包（在线程里跑）。`ZIP_STORED` 的理由见模块 docstring 第 3 条。"""
    with zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_STORED, allowZip64=True
    ) as archive:
        for arcname, path in entries:
            archive.write(path, arcname=arcname)


async def _stage(storage: ObjectStorage, key: str, folder: Path, index: int) -> Path | None:
    """把一条资产落到本地临时目录，返回可被 `zipfile.write` 读的路径。

    本地驱动直接复用原文件（零拷贝）；远端驱动必须先下载——`zipfile` 只认路径。
    下载**必须分块**：`get()` 会把整个对象留在进程里，2GB 的包只要两个并发就
    能把服务打挂，而且小文件完全测不出来（只有真实大素材才炸）。`iter_chunks()`
    的字节边读边写，进程内存与对象大小无关。
    """
    local = storage.local_path(key)
    if local is not None:
        return local if local.is_file() else None
    target = folder / f"item-{index}"
    try:
        handle = await asyncio.to_thread(target.open, "wb")
        try:
            async for chunk in storage.iter_chunks(key):
                # 写盘是阻塞的：一块 1MB 的 `write` 在事件循环里跑会把同 worker
                # 上的请求一起卡住（大文件时最明显），所以丢线程池。
                await asyncio.to_thread(handle.write, chunk)
        finally:
            await asyncio.to_thread(handle.close)
    except Exception:  # noqa: BLE001 - 单条取不到不该毁掉整包
        logger.warning("打包时取不到对象 key=%s", key)
        # 半个文件必须删掉：留着会被后续逻辑当成「已就绪」打进包里，
        # 用户拿到的是一个打不开的 zip（比少一条更难查）。
        await asyncio.to_thread(target.unlink, True)
        return None
    return target


async def build_download(job_id: str) -> None:
    """执行一条打包任务。幂等：只处理 `queued`。"""
    storage = get_storage()
    async with get_session_factory()() as session:
        row = await session.get(LibraryDownloadJob, job_id)
        if row is None:
            logger.info("打包任务已不存在，跳过 id=%s", job_id)
            return
        if row.status != QUEUED:
            logger.info("打包任务已是 %s，跳过 id=%s", row.status, job_id)
            return
        row.status = RUNNING
        await session.commit()

        asset_ids = [str(asset_id) for asset_id in (row.asset_ids or [])]
        notes: list[str] = []
        key = ""
        size = 0
        file_count = 0
        try:
            # 打包时**重新**判一遍可读性：建任务到真正压包之间可能隔几分钟，
            # 中间封装被取消共享、人被移出小组，都不该还能把字节打进包。
            # 判据用 `access.readable_rows`（与建任务时的 `_collect` 同一套），
            # 这里自己写一份必然漂移，而漂移方向只会是多给。
            by_id = await access.readable_rows(
                session, asset_ids, CurrentUser(id=row.owner_id, name="")
            )
            with tempfile.TemporaryDirectory(prefix="library-download-") as folder:
                root = Path(folder)
                entries: list[tuple[str, Path]] = []
                used: set[str] = set()
                for index, asset_id in enumerate(asset_ids, start=1):
                    asset = by_id.get(asset_id)
                    # 发起之后被删 / 被移进回收站 / 失权：跳过并记原因，不整包失败
                    if asset is None or asset.state != "active" or not asset.storage_key:
                        notes.append(f"{asset_id}: 已不可下载（无权或已删除）")
                        continue
                    path = await _stage(storage, asset.storage_key, root, index)
                    if path is None:
                        notes.append(f"{asset.name}: 文件读取失败")
                        continue
                    arcname = _unique_arcname(index, asset.name, used)
                    entries.append((arcname, path))

                if not entries:
                    raise RuntimeError("没有可打包的文件：" + "；".join(notes[:5]))

                target = root / "bundle.zip"
                await asyncio.to_thread(_zip_entries, target, entries)
                size = target.stat().st_size
                file_count = len(entries)
                key = f"{DOWNLOAD_PREFIX}/{row.id}.zip"
                await storage.put_file(key, target, "application/zip")
        except Exception as exc:  # noqa: BLE001 - 失败要落到行上，不能只写日志
            row.status = FAILED
            row.error = (str(exc) or type(exc).__name__)[:500]
            row.finished_at = datetime.now(UTC)
            row.expires_at = datetime.now(UTC) + timedelta(
                hours=settings.library_download_ttl_hours
            )
            await session.commit()
            logger.exception("打包失败 id=%s", job_id)
            return

        now = datetime.now(UTC)
        row.status = SUCCEEDED
        row.storage_key = key
        row.size_bytes = size
        row.error = "；".join(notes)[:500]
        row.finished_at = now
        row.expires_at = now + timedelta(hours=settings.library_download_ttl_hours)
        await session.commit()
        logger.info(
            "打包完成 id=%s files=%d bytes=%d skipped=%d",
            job_id,
            file_count,
            size,
            len(notes),
        )


def _unique_arcname(index: int, name: str, used: set[str]) -> str:
    """`序号_原名`；同名时补 `_2`、`_3`…（不同资产可能叫同一个名字）。"""
    base = _safe_name(name, f"asset-{index}")
    candidate = f"{index:03d}_{base}"
    if candidate not in used:
        used.add(candidate)
        return candidate
    stem, suffix = Path(base).stem, Path(base).suffix
    counter = 2
    while f"{index:03d}_{stem}_{counter}{suffix}" in used:
        counter += 1
    candidate = f"{index:03d}_{stem}_{counter}{suffix}"
    used.add(candidate)
    return candidate


async def enqueue_download(job_id: str) -> None:
    """入队。幂等键用任务 id：重复调用（用户连点）不会压两遍。"""
    await get_queue().enqueue(
        Job(
            name="library.download.build",
            payload={"id": job_id},
            idempotency_key=f"library.download:{job_id}",
            # 2GB 的包在慢盘上要几分钟，默认 300 秒不够
            timeout_sec=1800.0,
        )
    )


@register_handler("library.download.build")
async def handle_build(job: Job) -> None:
    await build_download(str(job.payload["id"]))


# --------------------------------------------------------------------------- #
# 过期清理
# --------------------------------------------------------------------------- #
@dataclass
class DownloadGcReport:
    expired: int = 0
    purged: int = 0
    failed: int = 0
    dry_run: bool = False
    ids: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [
            f"清理过期任务 {self.expired}，判失败 {self.failed}，删历史行 {self.purged}"
        ]


async def run_download_gc(
    *, batch: int = 200, dry_run: bool = False, now: datetime | None = None
) -> DownloadGcReport:
    """三轮：卡死的任务判失败 → 过期任务删字节 → 过期 7 天的行清掉。

    顺序有意义：先把卡在 `running` 的行判成 `failed`，它们才带得上 `expires_at`，
    下一轮才有机会被清掉。反过来会出现「一行永远 running、永远没有过期时间」。
    """
    moment = now or datetime.now(UTC)
    report = DownloadGcReport(dry_run=dry_run)
    storage = get_storage()
    async with get_session_factory()() as session:
        stuck = (
            await session.scalars(
                select(LibraryDownloadJob)
                .where(
                    LibraryDownloadJob.status.in_((QUEUED, RUNNING)),
                    LibraryDownloadJob.created_at <= moment - STUCK_AFTER,
                )
                .limit(batch)
            )
        ).all()
        expired = (
            await session.scalars(
                select(LibraryDownloadJob)
                .where(
                    LibraryDownloadJob.status.in_((SUCCEEDED, FAILED)),
                    LibraryDownloadJob.expires_at.is_not(None),
                    LibraryDownloadJob.expires_at <= moment,
                )
                .limit(batch)
            )
        ).all()
        ancient = (
            await session.scalars(
                select(LibraryDownloadJob)
                .where(
                    LibraryDownloadJob.status == EXPIRED,
                    LibraryDownloadJob.expires_at.is_not(None),
                    LibraryDownloadJob.expires_at <= moment - PURGE_AFTER,
                )
                .limit(batch)
            )
        ).all()

        for row in stuck:
            report.failed += 1
            report.ids.append(row.id)
            if dry_run:
                continue
            row.status = FAILED
            row.error = "任务超时未完成，请重新发起"
            row.finished_at = moment
            row.expires_at = moment + timedelta(hours=settings.library_download_ttl_hours)

        for row in expired:
            report.expired += 1
            report.ids.append(row.id)
            if dry_run:
                continue
            key = row.storage_key
            row.status = EXPIRED
            row.storage_key = ""
            if key:
                with contextlib.suppress(Exception):
                    await storage.delete(key)

        for row in ancient:
            report.purged += 1
            if dry_run:
                continue
            await session.execute(
                delete(LibraryDownloadJob).where(LibraryDownloadJob.id == row.id)
            )

        if not dry_run and (report.expired or report.purged or report.failed):
            await session.commit()
            await audit(
                "library.download.gc",
                target_type="library_download",
                detail={
                    "expired": report.expired,
                    "purged": report.purged,
                    "failed": report.failed,
                },
            )
    return report


@periodic(
    "library.download.gc",
    every=max(60.0, settings.library_download_gc_interval_sec),
    immediate=False,
    # 多实例同时扫会重复删同一批 key；删除幂等，但抢到一台就够了
    leader_only=True,
)
async def download_gc() -> None:
    if not settings.library_download_gc_enabled:
        logger.debug("打包清理未启用（LIBRARY_DOWNLOAD_GC_ENABLED=false），本轮跳过")
        return
    report = await run_download_gc()
    if report.expired or report.purged or report.failed:
        logger.info(
            "打包清理：过期 %d，判失败 %d，删行 %d",
            report.expired,
            report.failed,
            report.purged,
        )
