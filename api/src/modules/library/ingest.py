"""生成入库：`library-ingest` 注入口与 `POST /assets/from-generation` 的实现。

生成侧（image / template / task）与资产库之间只有这条通道，三个来源在入库那一刻
被**拉平成一件事**：一条 `state=draft` 的资产行 + 一份冻结的参数快照。

## 为什么落点固定是草稿箱

生成结果直接进个人库，等于把「模型跑出来的东西」与「用户挑过的东西」混成同一堆；
更糟的是共享出去之后，别人看到的是一张还没人看过的图。所以：

    state=draft, visibility=personal, origin=generated

协议不给调用方传 `state` / `visibility`——想直接入库的是用户操作，不是这里。

## 幂等与「内容相同」

两种命中，都返回 `created=False`：

1. `source_key` 已存在（生成侧轮询重复回调）——返回原来那一行；
2. 同一 owner、同 sha256、同 kind 且未删除（与上传的秒传同一口径）——
   上传时这条由 `uq_library_assets_owner_sha_kind` 唯一索引保证，入库必须
   走同一套判定，否则第二次生成同样的字节会撞 500。

第 2 种命中时不写参数快照：那一行已经有自己的来源了，把新来源覆盖上去
会让「这张图是谁生成的」变成最后一个回调说了算。

## 字节必须物化

三选一：`storageKey`（已在主对象存储里）、`sourceUrl`（服务端下载后再存）、
`archiveKey`（**归档存储**里的对象 key，由后端模块直接调用入库端口时填写）。
**不接受只存外部链接**：供应商签名地址会过期，详情页与下载都会裂。

`archiveKey` 是为模板库替换成片预留的：它的成片由 `template/worker.publish_output`
搬进 `ArchiveStorage`（`runs/{id}/outputs/generated.mp4`），与主对象存储不是同一个
驱动。这里统一物化后再写进资产库自己的 blob，资产因此不依赖归档盘的生命周期。
本批只提供能力，**不接生成链路**：`ReplacementPipeline.archive_request` 何时实现、
与 `replacement_reviews` 怎么分工，等产品口径。
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from collections.abc import Mapping
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser, get_current_user
from core.exceptions import BadRequest
from core.logging import get_logger
from core.ports.library_ingest import IngestRequest, IngestResult
from core.ports.library_lookup import LibraryAssetKind
from infra import get_storage
from infra.archive import get_archive_storage
from infra.http import HttpError, fetch_bytes
from infra.media_probe import EXTENSIONS, MAX_BYTES, create_preview, inspect
from infra.storage.base import ObjectStorage
from modules.library.errors import GenerationSourceInvalid, LibraryFileTooLarge
from modules.library.models import LibraryAsset, LibraryAssetGeneration
from modules.library.service import blob_key, preview_key
from modules.library.tags import set_asset_tags

__all__ = ["GenerationIngest", "SqlLibraryIngest"]

logger = get_logger(__name__)

_ALLOWED_KINDS = {LibraryAssetKind.IMAGE, LibraryAssetKind.VIDEO}

#: 快照字段名 → `LibraryAssetGeneration` 列名。刻意逐字段列出来，而不是
#: `**snapshot`：调用方给了一个拼错的键时，`**` 会静默丢掉它（dataclass 转 dict
#: 之后再过滤也一样），于是「参数丢了」永远没人发现。
_SNAPSHOT_FIELDS: tuple[tuple[str, str], ...] = (
    ("model", "model"),
    ("model_id", "model_id"),
    ("prompt", "prompt"),
    ("size", "size"),
    ("count", "count"),
    ("duration_sec", "duration_sec"),
    ("aspect_ratio", "aspect_ratio"),
    ("style_preset", "style_preset"),
    ("seed", "seed"),
    ("reference_asset_ids", "reference_asset_ids"),
    # v2 参考图。两个键写同一列不是笔误：生图流水线用的是契约名
    # `referenceLibraryIds`（它直接照抄出参），任务流水线用的是列名。以前前者
    # 被当成「不认识的键」静默丢掉，后者被写进上面那列 v1 字段——两边都错。
    ("reference_library_ids", "reference_library_ids"),
    ("referenceLibraryIds", "reference_library_ids"),
    ("pack_id", "pack_id"),
    ("pack_version", "pack_version"),
    ("prompt_spec", "prompt_spec"),
    ("configuration", "configuration"),
    ("external_id", "external_id"),
    ("credits", "credits"),
    ("generated_at", "generated_at"),
)


class GenerationIngest:
    """一次入库操作。构造时拿 session / storage，`run()` 自己 commit。"""

    def __init__(self, session: AsyncSession, storage: ObjectStorage) -> None:
        self.session = session
        self.storage = storage

    async def run(self, request: IngestRequest) -> IngestResult:
        if request.kind not in _ALLOWED_KINDS:
            raise GenerationSourceInvalid("生成入库只支持图片与视频")
        owner_id = request.owner_id or _context_owner()
        if not owner_id:
            raise GenerationSourceInvalid("无法确定归属人：请显式传 ownerId")

        source_key = request.source_key.strip()
        if not source_key:
            raise GenerationSourceInvalid("sourceKey 不能为空")

        existing = await self._by_source_key(source_key)
        if existing is not None:
            return IngestResult(asset_id=existing.id, created=False)

        # `archiveKey` 走 `ArchiveStorage.materialize()`：远端驱动交出的是临时缓存，
        # 退出上下文即回收，所以「物化 → 探测 → 写 blob」整段都得留在 ExitStack 里。
        with tempfile.TemporaryDirectory(prefix="library-ingest-") as folder, ExitStack() as stack:
            path, extension, mime = await self._materialize(request, Path(folder), stack)
            # ffprobe / PIL 都是阻塞的：入库可能同时来好几条，别把事件循环卡住
            info = await asyncio.to_thread(inspect, path, extension)
            digest, size = await asyncio.to_thread(_digest_and_size, path)

            duplicate = await self._dedupe(owner_id, digest, request.kind.value)
            if duplicate is not None:
                # 同一份字节已经在这个人的库里了：返回原行，不覆盖来源
                return IngestResult(asset_id=duplicate.id, created=False)

            key = blob_key(digest, extension)
            await self.storage.put_file(key, path, mime)
            cover_key = await self._ensure_preview(path, digest, request.kind.value)

            row = LibraryAsset(
                name=_asset_name(request),
                kind=request.kind.value,
                origin="generated",
                mime_type=mime,
                size_bytes=size,
                sha256=digest,
                storage_key=key,
                cover_key=cover_key,
                width=int(info["width"]),
                height=int(info["height"]),
                duration=float(info["duration"]),
                has_audio=bool(info["hasAudio"]),
                owner_id=owner_id,
                visibility="personal",
                state="draft",
                state_version=1,
                source_key=source_key,
                created_at=datetime.now(UTC),
            )
            self.session.add(row)
            await self.session.flush()
            self.session.add(
                LibraryAssetGeneration(
                    asset_id=row.id,
                    source_module=request.source_module,
                    source_task_id=request.source_task_id,
                    **_snapshot_values(request.generation),
                )
            )
            if request.tag_ids:
                # tagIds 来自生成侧的配置，不是用户在界面上打标；不让「某个
                # 一级后来成了分组」把生成结果入库整条链路卡住。
                await set_asset_tags(
                    self.session, row.id, request.tag_ids, reject_groups=False
                )
            try:
                await self.session.commit()
            except IntegrityError:
                # 并发回调同一个 sourceKey：唯一索引兜底，回滚后按同一口径再查一次
                await self.session.rollback()
                raced = await self._by_source_key(source_key) or await self._dedupe(
                    owner_id, digest, request.kind.value
                )
                if raced is None:
                    raise
                return IngestResult(asset_id=raced.id, created=False)
            return IngestResult(asset_id=row.id, created=True)

    # ------------------------------------------------------------------ 内部

    async def _materialize(
        self, request: IngestRequest, folder: Path, stack: ExitStack
    ) -> tuple[Path, str, str]:
        """把字节物化成可读的本地文件，返回 (路径, 扩展名, mime)。

        `storageKey` 优先：它已经在主存储里，本地驱动能零拷贝。
        `archiveKey` 走归档驱动的 `materialize()`，`stack` 负责在整段用完前
        不回收远端驱动的临时缓存。
        `sourceUrl` 走共享的 `fetch_bytes`（逐跳 SSRF 校验 + 大小上限）。
        """
        extension = _extension_of(request)
        if request.storage_key:
            local = self.storage.local_path(request.storage_key)
            if local is not None:
                if not local.is_file():
                    raise GenerationSourceInvalid(
                        details={"storageKey": request.storage_key}
                    )
                return local, extension, EXTENSIONS[extension]
            try:
                payload = await self.storage.get(request.storage_key)
            except Exception as exc:  # noqa: BLE001 - 见下
                # 取不到字节就是「调用方给的 key 不对」：生成侧要能读到这条
                # 明确失败并重试，而不是被当成一次 502 的存储故障。
                raise GenerationSourceInvalid(
                    details={"storageKey": request.storage_key}
                ) from exc
        elif request.archive_key:
            # 归档盘与主对象存储是两个驱动。materialize() 交出的本地路径在远端
            # 实现里只是缓存，必须由 stack 持有到 put_file / 预览派生结束。
            try:
                path = stack.enter_context(
                    get_archive_storage().materialize(request.archive_key)
                )
            except Exception as exc:  # noqa: BLE001 - 驱动异常一律转成入参错误
                raise GenerationSourceInvalid(
                    details={"archiveKey": request.archive_key, "reason": str(exc)}
                ) from exc
            if not path.is_file():
                raise GenerationSourceInvalid(details={"archiveKey": request.archive_key})
            if path.stat().st_size > MAX_BYTES:
                raise LibraryFileTooLarge(details={"maxBytes": MAX_BYTES})
            return path, extension, EXTENSIONS[extension]
        elif request.source_url:
            try:
                payload = (
                    await fetch_bytes(request.source_url, max_bytes=MAX_BYTES, retries=2)
                ).data
            except HttpError as exc:
                raise GenerationSourceInvalid(
                    details={"sourceUrl": request.source_url, "reason": str(exc)}
                ) from exc
        else:
            raise GenerationSourceInvalid()
        if len(payload) > MAX_BYTES:
            raise LibraryFileTooLarge(details={"maxBytes": MAX_BYTES})
        target = folder / f"source{extension}"
        # 300MB 写盘同样别占着事件循环
        await asyncio.to_thread(target.write_bytes, payload)
        return target, extension, EXTENSIONS[extension]

    async def _ensure_preview(self, source: Path, digest: str, kind: str) -> str:
        with tempfile.TemporaryDirectory(prefix="library-ingest-cover-") as folder:
            target = Path(folder) / "cover.jpg"
            ok = await asyncio.to_thread(create_preview, source, target, kind)
            if not ok or not target.is_file():
                logger.info("生成结果没有缩略图：%s", digest[:12])
                return ""
            key = preview_key(digest)
            await self.storage.put_file(key, target, "image/jpeg")
        return key

    async def _by_source_key(self, source_key: str) -> LibraryAsset | None:
        return await self.session.scalar(
            select(LibraryAsset).where(LibraryAsset.source_key == source_key)
        )

    async def _dedupe(self, owner_id: str, digest: str, kind: str) -> LibraryAsset | None:
        return await self.session.scalar(
            select(LibraryAsset).where(
                LibraryAsset.owner_id == owner_id,
                LibraryAsset.sha256 == digest,
                LibraryAsset.kind == kind,
                LibraryAsset.deleted_at.is_(None),
            )
        )


class SqlLibraryIngest:
    """注入口实现：无状态，session 由调用方传（与其业务事务同一个快照）。

    **存储不在这里固化**：`get_storage()` 是进程级缓存，但测试会 `reset_infra_cache()`
    换驱动，而注入口是 import 期注册的。把实例存下来的话，切换驱动之后这个端口
    还会往旧根目录写。每次调用现取，代价是一次 lru_cache 命中。
    """

    async def ingest_generated(
        self, session: AsyncSession, request: IngestRequest
    ) -> IngestResult:
        return await GenerationIngest(session, get_storage()).run(request)


# --------------------------------------------------------------------------- #
# 入参映射
# --------------------------------------------------------------------------- #
def _snapshot_values(snapshot: Any) -> dict[str, Any]:
    """把入参快照映射成列值，**只取认得的字段**（见 `_SNAPSHOT_FIELDS`）。

    `None` 一律视为「没给」，只有 `generated_at` 例外（它本来就可空）：
    其余列都是 NOT NULL，把 None 写进去只会在 commit 时炸成 500。

    同一列有两个入参写法时（`reference_library_ids` / `referenceLibraryIds`），
    两个都给且值不同直接 400：静默取最后一个会让「档案里记的是哪几张图」取决于
    键在字典里的顺序，而这种错查不出来。
    """
    if snapshot is None:
        return {}
    values: dict[str, Any] = {}
    for source, column in _SNAPSHOT_FIELDS:
        if isinstance(snapshot, Mapping):
            value = snapshot.get(source)
        else:
            value = getattr(snapshot, source, None)
        if value is None and column not in _NULLABLE_SNAPSHOT:
            continue
        if column in values and values[column] != value:
            raise BadRequest(f"快照里 {column} 出现了两个不同的取值，请只传一种写法")
        values[column] = value
    return values


#: 允许显式传 `None` 的快照列（其余 None 视为「没给」，不覆盖默认值）
_NULLABLE_SNAPSHOT = {"generated_at"}


def _context_owner() -> str:
    user: CurrentUser | None = get_current_user()
    return user.id if user else ""


def _digest_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _extension_of(request: IngestRequest) -> str:
    """推断扩展名：mime → 名字 / storageKey / archiveKey / URL 的后缀。推不出来就 400。

    不信任调用方给的 `mimeType` 单独成立：它与内容的一致性由 `inspect()` 校验，
    这里只是先决定用哪个解析器。
    """
    by_mime = {mime: ext for ext, mime in EXTENSIONS.items()}
    if request.mime_type:
        extension = by_mime.get(request.mime_type)
        if extension is None:
            raise GenerationSourceInvalid(
                details={"mimeType": request.mime_type, "supported": sorted(by_mime)}
            )
        return extension
    for candidate in (
        request.name,
        request.storage_key,
        request.archive_key,
        request.source_url,
    ):
        suffix = Path(candidate.split("?", 1)[0]).suffix.lower()
        if suffix in EXTENSIONS:
            return suffix
    raise GenerationSourceInvalid("无法从 mimeType / 文件名推断格式")


def _asset_name(request: IngestRequest) -> str:
    """名字优先级：调用方给的 → 文件名 → `来源-任务号`。"""
    for candidate in (
        request.name,
        Path(request.storage_key).name,
        Path(request.archive_key).name,
    ):
        cleaned = Path(candidate).name.strip() if candidate else ""
        if cleaned:
            return cleaned[:200]
    return f"{request.source_module}-{request.source_task_id}"[:200]
