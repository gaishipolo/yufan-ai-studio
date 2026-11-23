"""批量下载：异步打包 + 流式取件（设计 §6）。

## 为什么不在请求线程里压 zip

`zipfile` 是同步阻塞的，几十上百 MB 的读取 + 写盘会把整个 worker 拖住；而这个
项目的请求线程本来就短命（每个请求一个协程），压包期间连 `/health/ready` 都要排队。
所以走「建任务 → 入队 → 后台压 → 轮询 → 取件」，与导出 / 模板替换同一个模型。

## 三条口径

1. **只打包 `state=active` 的条目**：草稿与回收站在语义上「还没有 / 已经不属于」
   库内内容，把它们打进包会让「删除」变成只删了个标签。逐条给原因，不整单失败。
2. **闸门在选完之后**：请求里最多 200 个 id，但真正限制的是
   **实际进包的条目数（100）与解压后总量（2GB）**。按资产行登记的 `size_bytes` 求和，
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
import re
import tempfile
import zipfile
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
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
    LibraryDownloadJobAsset,
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
            asset_count=len(selected),
            status=QUEUED,
        )
        self.session.add(job)
        # 资产清单写进关联表（不是 JSON 列）：引用检查要靠它判断「这条资产
        # 能不能彻底删除」，那必须是一次精确的 `IN` 查询，而不是在 JSON
        # 文本里做子串粗筛（粗筛漏查 = 没人引用 = 不可逆删除放行）。
        await self.session.flush()
        for position, row in enumerate(selected):
            self.session.add(
                LibraryDownloadJobAsset(
                    job_id=job.id,
                    asset_id=row.id,
                    position=position,
                )
            )
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
        """逐个判定「能不能进包」。返回 (可打包, 跳过明细)。"""
        wanted = list(dict.fromkeys(ids))
        rows = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(LibraryAsset).where(LibraryAsset.id.in_(wanted))
                )
            ).all()
        }
        # 封装要**展开成成员**再打包：它自己没有字节，直接按行处理会得到一条
        # 「没有可下载的原文件」，而用户勾的是「这一套图」。成员不再单列之后，
        # 这个入口也是那几张图唯一的批量下载途径。
        pack_ids = [row.id for row in rows.values() if row.kind == "pack"]
        members_by_pack = await self._pack_members(pack_ids)
        member_rows = [member for group in members_by_pack.values() for member in group]
        # 与选择器 / 取件同口径：可见封装的成员即使仍是别人的 personal 也能打包。
        # 一次算完，不在循环里逐行查权限。
        readable = await access.readable_rows(
            self.session,
            [row.id for row in rows.values()] + [member.id for member in member_rows],
            actor,
            via_pack=True,
        )

        selected: list[LibraryAsset] = []
        skipped: list[DownloadSkip] = []
        for asset_id in wanted:
            row = rows.get(asset_id)
            if row is None or row.id not in readable:
                # 预览能看、单条能下，批量却跳过，用户只会觉得「打包坏了」
                skipped.append(DownloadSkip(id=asset_id, reason="不存在或无权访问"))
                continue

            if row.kind == "pack":
                members = members_by_pack.get(row.id, [])
                if not members:
                    skipped.append(
                        DownloadSkip(id=row.id, name=row.name, reason="封装里没有可用素材")
                    )
                    continue
                for member in members:
                    # 成员逐条判「能不能进包」，但原因要带上是哪个包 ——
                    # 只说「回收站内容不可下载」而卡片上明明是个封装，对不上。
                    reason = _skip_reason(member) or (
                        "" if member.id in readable else "不存在或无权访问"
                    )
                    if reason:
                        skipped.append(
                            DownloadSkip(
                                id=member.id,
                                name=member.name,
                                reason=f"来自封装「{row.name}」：{reason}",
                            )
                        )
                        continue
                    selected.append(member)
                continue

            reason = _skip_reason(row)
            if reason:
                skipped.append(DownloadSkip(id=row.id, name=row.name, reason=reason))
                continue
            selected.append(row)

        return _dedupe(selected), skipped

    async def _pack_members(
        self, pack_ids: list[str]
    ) -> dict[str, list[LibraryAsset]]:
        """一批包各自的成员，按包内顺序。**不过滤已删的** —— 由 `_skip_reason`
        逐条判并给出原因，比在这里静默丢掉更容易解释。"""
        wanted = [item for item in dict.fromkeys(pack_ids) if item]
        if not wanted:
            return {}
        rows = (
            await self.session.execute(
                select(LibraryPackItem, LibraryAsset)
                .join(LibraryAsset, LibraryAsset.id == LibraryPackItem.asset_id)
                .where(LibraryPackItem.pack_id.in_(wanted))
                .order_by(LibraryPackItem.pack_id, LibraryPackItem.position)
            )
        ).all()
        result: dict[str, list[LibraryAsset]] = {}
        for item, asset in rows:
            result.setdefault(item.pack_id, []).append(asset)
        return result

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
            asset_count=row.asset_count,
            size_bytes=row.size_bytes,
            error=row.error,
            download_url=download_url,
            expires_at=row.expires_at,
            created_at=row.created_at,
            finished_at=row.finished_at,
            skipped=skipped or [],
        )


def _skip_reason(row: LibraryAsset) -> str:
    """不能进包的原因。空串 = 可以打包。"""
    if row.state == "draft":
        return "草稿未入库"
    if row.state == "trash":
        return "回收站内容不可下载"
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
        # **抢状态**：条件更新（`WHERE status='queued'`）。
        # 原来这里是「读行 → 判 queued → 置 running → commit」，两个 worker 同时
        # 读到 queued 就会各自压一遍包（同一个 key 互相覆盖、CPU 与磁盘翻倍）。
        # 队列的原子领取只保证「谁领到 job 行」，不保证旧持有者停止写业务表——
        # 模块自己的注释就写着「db 驱动在租约丢失后会重投」。
        if not await _claim_job(session, job_id):
            logger.info("打包任务已被别人领走，跳过 id=%s", job_id)
            return

        # 清单在关联表里，`position` 就是包内顺序（包内文件按 001_名 命名）
        asset_ids = [
            str(asset_id)
            for asset_id in (
                await session.execute(
                    select(LibraryDownloadJobAsset.asset_id)
                    .where(LibraryDownloadJobAsset.job_id == row.id)
                    .order_by(LibraryDownloadJobAsset.position)
                )
            ).scalars().all()
        ]
        notes: list[str] = []
        key = ""
        size = 0
        file_count = 0
        try:
            # 打包时**重新**判一遍可读性：建任务到真正压包之间可能隔几分钟，
            # 中间共享被取消、人被移出小组，都不该还能把字节打进包。
            # 判据用 `access.readable_rows`（与建任务时的 `_collect` 同一套），
            # 这里自己写一份必然漂移，而漂移方向只会是多给。
            by_id = await access.readable_rows(
                session, asset_ids, CurrentUser(id=row.owner_id, name=""), via_pack=True
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
            # 条件更新：只覆盖**自己抢到的那一次**运行（状态仍是 running）。
            # 原来这里无条件 `row.status = FAILED`，会把别的 worker 已经提交
            # 成功的行一起覆盖掉——用户看到「打包失败」，而 GC 之后按 failed 把
            # 那个其实可用的 zip 删掉。
            await _finish_job(
                session,
                job_id,
                status=FAILED,
                error=(str(exc) or type(exc).__name__)[:500],
                finished_at=datetime.now(UTC),
                expires_at=datetime.now(UTC)
                + timedelta(hours=settings.library_download_ttl_hours),
            )
            logger.exception("打包失败 id=%s", job_id)
            return

        now = datetime.now(UTC)
        landed = await _finish_job(
            session,
            job_id,
            status=SUCCEEDED,
            storage_key=key,
            size_bytes=size,
            error="；".join(notes)[:500],
            finished_at=now,
            expires_at=now + timedelta(hours=settings.library_download_ttl_hours),
        )
        if not landed:
            # 行已经不在 `running`（GC 判了失败、或被别的 worker 处理了），
            # 这次运行的结果**没有落库** —— 刚上传的 zip 就没有任何人认领。
            # GC 按数据库记录清理，孤儿不在其中，它会一直躺在桶里（最多 2GB）。
            # 删掉它，宁可让用户重新打包。
            with suppress(Exception):
                await storage.delete(key)
            logger.warning(
                "打包结果未能落库，已清理孤儿 zip id=%s key=%s bytes=%d",
                job_id,
                key,
                size,
            )
            return
        logger.info(
            "打包完成 id=%s files=%d bytes=%d skipped=%d",
            job_id,
            file_count,
            size,
            len(notes),
        )


async def _claim_job(session: AsyncSession, job_id: str) -> bool:
    """把任务从 `queued` 抢成 `running`。抢不到返回 False（别人已经在跑了）。

    「抢」用的是条件更新而不是「读 → 判 → 写」：只有一条语句能同时把
    `status='queued'` 改成 `running`，rowcount 就是这次是不是第一个。
    """
    claimed = cast(
        "CursorResult[Any]",
        await session.execute(
            update(LibraryDownloadJob)
            .where(
                LibraryDownloadJob.id == job_id,
                LibraryDownloadJob.status == QUEUED,
            )
            .values(status=RUNNING)
            .execution_options(synchronize_session=False)
        ),
    )
    await session.commit()
    return claimed.rowcount == 1


async def _finish_job(
    session: AsyncSession,
    job_id: str,
    *,
    status: str,
    storage_key: str | None = None,
    size_bytes: int | None = None,
    error: str | None = None,
    finished_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> bool:
    """把任务写成终态。**条件更新**：只覆盖仍在 `running` 的那一次运行。

    失败分支尤其要这样：原来它无条件把整行改成 failed，连 `storage_key` 一起
    清空，于是「另一个 worker 已经压成功」的那一行会被覆盖成失败——用户看到
    「打包失败」，GC 之后又按 failed 把那个其实可用的 zip 删掉。
    """
    values: dict[str, Any] = {"status": status}
    if storage_key is not None:
        values["storage_key"] = storage_key
    if size_bytes is not None:
        values["size_bytes"] = size_bytes
    if error is not None:
        values["error"] = error
    if finished_at is not None:
        values["finished_at"] = finished_at
    if expires_at is not None:
        values["expires_at"] = expires_at
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            update(LibraryDownloadJob)
            .where(
                LibraryDownloadJob.id == job_id,
                LibraryDownloadJob.status == RUNNING,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        ),
    )
    await session.commit()
    return result.rowcount == 1


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
            if key:
                # **先删字节、成功后再清 key**。
                # 原来这里是「先 `row.storage_key = ""` 再删字节」，而删除异常
                # 被 `contextlib.suppress` 吞掉：事务照常提交，这一行变成
                # expired 且没有 key，而下一轮 GC 只选 succeeded/failed —— 那个
                # 最大 2GB 的 zip 就永久留在对象存储里，没有任何记录还知道它存在。
                try:
                    await storage.delete(key)
                except Exception:  # noqa: BLE001 - 删失败要重试，不能吞掉
                    # 保留 key 与状态：这一行仍会被下一轮 GC 选中，字节还有得救。
                    logger.warning("删除过期 zip 失败，下一轮重试 id=%s key=%s", row.id, key)
                    continue
            row.status = EXPIRED
            row.storage_key = ""

        for row in ancient:
            report.purged += 1
            if dry_run:
                continue
            # 关联行一并删掉：关联表**刻意没加外键**（本模块的约定是关系由
            # 应用层维护），所以级联删除得自己写。
            #
            # 不写会留下孤儿行（任务行没了，关联行还在）。注意后果的边界：
            # 引用检查那边是 **INNER JOIN**（关联行必须对得上任务行），所以孤儿
            # 行**不会**被误报成「这个资产还被下载任务引用」——它只是垃圾数据，
            # 永远躺在索引里没人清理。所以这段代码是数据卫生，不是正确性。
            await session.execute(
                delete(LibraryDownloadJobAsset).where(
                    LibraryDownloadJobAsset.job_id == row.id
                )
            )
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
