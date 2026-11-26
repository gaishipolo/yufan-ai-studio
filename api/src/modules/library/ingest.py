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

**幂等键是按人隔离的**：`uq_library_assets_source_key` 是全局唯一索引，只按
key 查会让 B 命中 A 的行（B 的字节从未入库，返回的却是 A 的资产 id）。命中但
owner 不一致时直接 409，见 `_by_source_key`。

## 字节必须物化

三选一：`storageKey`（已在主对象存储里）、`sourceUrl`（服务端下载后再存）、
`archiveKey`（**归档存储**里的对象 key，由后端模块直接调用入库端口时填写）。
**不接受只存外部链接**：供应商签名地址会过期，详情页与下载都会裂。

`archiveKey` 是为模板库替换成片预留的：它的成片由 `template/worker.publish_output`
搬进 `ArchiveStorage`（`runs/{id}/outputs/generated.mp4`），与主对象存储不是同一个
驱动。这里统一物化后再写进资产库自己的 blob，资产因此不依赖归档盘的生命周期。
本批只提供能力，**不接生成链路**：`ReplacementPipeline.archive_request` 何时实现、
与 `replacement_reviews` 怎么分工，等产品口径。

## 存储键的归属

`storageKey` / `archiveKey` 是**调用方给的对象存储地址**。不校验归属就开放它们，
这个入口就是一个「任意对象读取」原语：主存储里装的是所有人的私有原片，
知道 key 就能把别人的字节读成自己名下的素材，随后 `GET /assets/{id}/file`
就把它拿走了。

所以按调用方分两种口径（`run(trusted=...)`）：

- **HTTP 端点**（`trusted=False`）：`storageKey` 必须指向调用方自己名下的
  资产行；`archiveKey` 直接不收（归档盘是另一个驱动，没有资产行可反查归属）。
- **`library-ingest` 端口**（`trusted=True`）：调用方是服务端模块，键是它
  自己刚写进去的，归属由它负责。

两条路径的字节来源一样，差的是「这个键能不能直接读」。
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from collections.abc import Mapping
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser, get_current_user
from core.exceptions import BadRequest, Conflict, Forbidden
from core.logging import get_logger
from core.ports.library_ingest import IngestRequest, IngestResult
from core.ports.library_lookup import LibraryAssetKind, get_library_lookup
from infra import get_storage
from infra.archive import get_archive_storage
from infra.driver_config import DriverSettings, driver_config
from infra.env_settings import vendor_settings
from infra.http import HttpError, fetch_to_file, validate_public_url
from infra.media_probe import EXTENSIONS, MAX_BYTES, create_preview, inspect
from infra.storage.base import ObjectStorage
from modules.library.errors import (
    GenerationSourceInvalid,
    IngestKeyNotOwned,
    LibraryFileTooLarge,
)
from modules.library.models import LibraryAsset, LibraryAssetGeneration
from modules.library.service import blob_key, preview_key
from modules.library.tags import (
    assignable_tag_ids,
    set_asset_tags,
    tag_ids_by_asset,
)


class IngestDownloadSettings(DriverSettings):
    """入库时「服务端替你去下载」的部署参数（前缀 `LIBRARY_INGEST_`）。

    `sourceUrl` 是**调用方给的地址**，服务端拿它出站取字节——不设白名单时只能
    退回 `validate_public_url` 的黑名单兜底，而那个兜底**不解析 DNS**：域名指向
    内网时拦不住。持 `library:generation:ingest` 的账号就能让服务端替他访问内网。

    生产必须显式配域名白名单（`prod_required_fields`），与成图落盘
    （`IMAGE_ASSET_ALLOWED_HOSTS`）同一条口径：宁可启动失败，也不要在生产里
    靠兜底过日子，那种状态日志里一句错都没有。
    """

    model_config = driver_config("LIBRARY_INGEST")

    prod_required_fields: ClassVar[tuple[str, ...]] = ("allowed_hosts",)

    # 逗号分隔的域名白名单；留空时只拒绝非公网 IP 字面量与明显的内网域名
    allowed_hosts: str = ""
    # 这是**下载**超时，不是提交耗时，所以不声明 submit_budget_for
    timeout_sec: float = 120.0


# 配置热生效：与供应商配置同一套 mtime 指纹缓存。白名单写进 .env 后下一次
# 入库即生效 —— 否则「补上 CDN 域名还得重启」正是这次漏配事故的延续。
get_ingest_download_settings = vendor_settings(
    IngestDownloadSettings, env_prefix="LIBRARY_INGEST"
)


def _allowed_hosts() -> frozenset[str]:
    raw = get_ingest_download_settings().allowed_hosts
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def _guard_source_url(url: str) -> None:
    """`sourceUrl` 的出站校验：配了白名单就只认白名单，没配走黑名单兜底。"""
    validate_public_url(url, allowed_hosts=_allowed_hosts())


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
    # 参考图。两个键写同一列不是笔误：生图流水线用的是契约名
    # `referenceLibraryIds`（它直接照抄出参），任务流水线用的是列名。
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


def _snapshot_reference_ids(generation: Mapping[str, object]) -> list[str]:
    """读快照里的参考素材 id。两个键名都要认 —— 生图侧照抄契约的
    `referenceLibraryIds`，任务侧用列名，与 `_SNAPSHOT_FIELDS` 同源。"""
    refs = generation.get("reference_library_ids") or generation.get(
        "referenceLibraryIds"
    )
    if isinstance(refs, str):
        refs = [refs]
    if not isinstance(refs, (list, tuple)):
        return []
    return [item for item in refs if isinstance(item, str) and item]


async def _inherited_tag_ids(
    session: AsyncSession, generation: Mapping[str, object]
) -> tuple[str, ...]:
    """从生成快照里的参考素材继承标签（**并集**）。

    快照是冻结的：读的是「提交这次生成时用了哪些素材」，不会因后来有人改了任务
    或删了素材而漂移。

    只继承**可打标**的词：维度标签（有子级的一级）显示成一个标签却筛不中，
    继承它等于给用户一个假标签。判据走 `assignable_tag_ids`，与界面打标同一口径。
    """
    asset_ids = _snapshot_reference_ids(generation)
    if not asset_ids:
        return ()

    by_asset = await tag_ids_by_asset(session, asset_ids)
    collected = {tag_id for tags in by_asset.values() for tag_id in tags}
    if not collected:
        return ()
    return tuple(sorted(await assignable_tag_ids(session, collected)))


class GenerationIngest:
    """一次入库操作。构造时拿 session / storage，`run()` 自己 commit。"""

    def __init__(self, session: AsyncSession, storage: ObjectStorage) -> None:
        self.session = session
        self.storage = storage

    async def _assert_references_readable(
        self, generation: Mapping[str, object], owner_id: str
    ) -> None:
        """HTTP 入口：快照里的参考素材必须是调用方现在还能读的。

        `library-ingest` 端口（trusted）由服务端生成链路填，受理期已经过
        `core.library_refs` 的判定，这里不重复。HTTP 端点的 `generation`
        完全由调用方自填；不校验就成了「猜一个 id 借别人的标签」——标签是
        全局词表不敏感，但产物会带着它根本没用过的素材的标签。

        判据走 `readable_ids`（含小组共享），按 owner 判会误拒别人共享给自己的
        素材。不查 `state`：参考素材可能在生成后被删，那时仍按快照继承，
        不能让入库失败反噬已经成功的生成。
        """
        asset_ids = _snapshot_reference_ids(generation)
        if not asset_ids:
            return
        actor = get_current_user()
        if actor is None or actor.id != owner_id:
            raise Forbidden("无法确定参考素材归属，无法入库")
        readable = await get_library_lookup().readable_ids(
            self.session, actor, asset_ids
        )
        denied = [asset_id for asset_id in asset_ids if asset_id not in readable]
        if denied:
            raise Forbidden(
                f"无权限使用这些参考素材（可能是他人的私有素材）：{denied}"
            )

    async def run(self, request: IngestRequest, *, trusted: bool = False) -> IngestResult:
        """执行一次入库。

        `trusted` 标的是**调用方是不是服务端内部代码**：

        - `False`（HTTP 端点 `POST /assets/from-generation`）：`storageKey` /
          `archiveKey` 由调用方给，服务端必须自己校验归属，否则这个端点就是
          一个「任意对象读取」原语（见 `_assert_own_key`）；
        - `True`（`library-ingest` 端口）：调用方是服务端模块，键是它自己刚写
          进去的，归属由它自己负责。

        两条路径的**字节来源**一样，口径不同的是「这个键能不能直接读」。
        """
        if request.kind not in _ALLOWED_KINDS:
            raise GenerationSourceInvalid("生成入库只支持图片与视频")
        owner_id = request.owner_id or _context_owner()
        if not owner_id:
            raise GenerationSourceInvalid("无法确定归属人：请显式传 ownerId")

        source_key = request.source_key.strip()
        if not source_key:
            raise GenerationSourceInvalid("sourceKey 不能为空")
        if not trusted:
            await self._assert_references_readable(request.generation, owner_id)

        existing = await self._by_source_key(source_key, owner_id)
        if existing is not None:
            return IngestResult(asset_id=existing.id, created=False)

        # `archiveKey` 走 `ArchiveStorage.materialize()`：远端驱动交出的是临时缓存，
        # 退出上下文即回收，所以「物化 → 探测 → 写 blob」整段都得留在 ExitStack 里。
        with tempfile.TemporaryDirectory(prefix="library-ingest-") as folder, ExitStack() as stack:
            path, extension, mime = await self._materialize(
                request, Path(folder), stack, trusted=trusted, owner_id=owner_id
            )
            # ffprobe / PIL 都是阻塞的：入库可能同时来好几条，别把事件循环卡住
            info = await asyncio.to_thread(inspect, path, extension)
            digest, size = await asyncio.to_thread(_digest_and_size, path)

            duplicate = await self._dedupe(owner_id, digest, request.kind.value)
            if duplicate is not None and (
                not request.replaces_asset_id or duplicate.id != request.replaces_asset_id
            ):
                # 同一份字节已经在这个人的库里了（且不是正在原地替换的自身）：
                # 若强行把 replaces_asset_id 改成同一摘要，会撞 `uq_library_assets_owner_sha_kind`。
                return IngestResult(asset_id=duplicate.id, created=False)

            key = blob_key(digest, extension)
            await self.storage.put_file(key, path, mime)
            cover_key = await self._ensure_preview(path, digest, request.kind.value)

            if request.replaces_asset_id:
                # 原地替换（去字幕版）：同一个 asset id，字节/封面/时长换成新的。
                # 为什么不是「新建一条再删旧的」：模板、镜头、分享链接里引用的是
                # asset id，换 id 等于把那些引用全指向一条已删除的资产。
                try:
                    replaced = await self._replace(
                        request.replaces_asset_id,
                        owner_id=owner_id,
                        values={
                            "mime_type": mime,
                            "size_bytes": size,
                            "sha256": digest,
                            "storage_key": key,
                            "cover_key": cover_key,
                            "width": int(info["width"]),
                            "height": int(info["height"]),
                            "duration": float(info["duration"]),
                            "has_audio": bool(info["hasAudio"]),
                            "name": _asset_name(request),
                        },
                    )
                    if replaced is not None:
                        await self.session.commit()
                        return IngestResult(asset_id=replaced.id, created=False)
                except IntegrityError:
                    await self.session.rollback()
                    raced = await self._by_source_key(
                        source_key, owner_id
                    ) or await self._dedupe(owner_id, digest, request.kind.value)
                    if raced is None:
                        raise
                    return IngestResult(asset_id=raced.id, created=False)

            if duplicate is not None:
                return IngestResult(asset_id=duplicate.id, created=False)

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
            inherited = await _inherited_tag_ids(self.session, request.generation)
            tag_ids = list(dict.fromkeys([*request.tag_ids, *inherited]))
            if tag_ids:
                # 配置里的 tagIds + 参考素材继承来的词都不是用户在界面上打标；
                # 不让「某个一级后来成了分组」把生成结果入库整条链路卡住。
                await set_asset_tags(
                    self.session, row.id, tag_ids, reject_groups=False
                )
            try:
                await self.session.commit()
            except IntegrityError:
                # 并发回调同一个 sourceKey：唯一索引兜底，回滚后按同一口径再查一次
                await self.session.rollback()
                raced = await self._by_source_key(
                    source_key, owner_id
                ) or await self._dedupe(
                    owner_id, digest, request.kind.value
                )
                if raced is None:
                    raise
                return IngestResult(asset_id=raced.id, created=False)
            return IngestResult(asset_id=row.id, created=True)

    # ------------------------------------------------------------------ 内部

    async def _materialize(
        self,
        request: IngestRequest,
        folder: Path,
        stack: ExitStack,
        *,
        trusted: bool,
        owner_id: str,
    ) -> tuple[Path, str, str]:
        """把字节物化成可读的本地文件，返回 (路径, 扩展名, mime)。

        `storageKey` 优先：它已经在主存储里，本地驱动能零拷贝。
        `archiveKey` 走归档驱动的 `materialize()`，`stack` 负责在整段用完前
        不回收远端驱动的临时缓存。
        `sourceUrl` 走 `fetch_to_file`：逐跳 SSRF 校验（配了白名单就只认白名单）+ 大小上限，
        并且流式写盘——成片允许 300MB，整份读进内存会把进程撑爆。

        HTTP 侧（非 trusted）给的 `storageKey` / `archiveKey` 先过归属校验：
        这两个键是**调用方给的对象存储地址**，不校验就等于开放「读主存储里的
        任意对象」。详见 `_assert_own_key`。
        """
        extension = _extension_of(request)
        if request.storage_key:
            if not trusted:
                await self._assert_own_key(request.storage_key, owner_id)
            local = self.storage.local_path(request.storage_key)
            if local is not None:
                if not local.is_file():
                    raise GenerationSourceInvalid(
                        details={"storageKey": request.storage_key}
                    )
                return local, extension, EXTENSIONS[extension]
            # 远端驱动：**分块**落盘。`get()` 会把整份对象（最大 300MB）读进
            # 内存，峰值是「并发数 × 文件大小」；体积上限也必须在流的过程中累计
            # 判定，整份读完再判，峰值已经发生了。
            target = folder / f"source{extension}"
            try:
                await self._stream_to_file(request.storage_key, target)
            except LibraryFileTooLarge:
                raise
            except Exception as exc:  # noqa: BLE001 - 见下
                # 取不到字节就是「调用方给的 key 不对」：生成侧要能读到这条
                # 明确失败并重试，而不是被当成一次 502 的存储故障。
                raise GenerationSourceInvalid(
                    details={"storageKey": request.storage_key}
                ) from exc
            return target, extension, EXTENSIONS[extension]
        elif request.archive_key:
            if not trusted:
                # 归档盘是另一个驱动，没有资产行可反查归属：HTTP 端点不收归档键。
                raise IngestKeyNotOwned(
                    "archiveKey 只对内部调用方开放，HTTP 端点不接受归档键",
                    details={"archiveKey": request.archive_key},
                )
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
            target = folder / f"source{extension}"
            try:
                # 流式落盘：这条链允许 300MB 的成片，整份读进内存的峰值是
                # 「并发数 × 文件大小」。白名单见 `IngestDownloadSettings`。
                fetched = await fetch_to_file(
                    request.source_url,
                    target,
                    max_bytes=MAX_BYTES,
                    retries=2,
                    timeout=get_ingest_download_settings().timeout_sec,
                    guard=_guard_source_url,
                )
            except (HttpError, ValueError) as exc:
                # `ValueError` 是 guard 抛的（协议不可用 / host 不在白名单）
                raise GenerationSourceInvalid(
                    details={"sourceUrl": request.source_url, "reason": str(exc)}
                ) from exc
            if fetched.size > MAX_BYTES:
                raise LibraryFileTooLarge(details={"maxBytes": MAX_BYTES})
            return target, extension, EXTENSIONS[extension]
        raise GenerationSourceInvalid()

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

    async def _stream_to_file(self, key: str, target: Path) -> int:
        """分块把对象写进本地文件，返回落盘字节数。

        体积上限在**流的过程中**累计判定：整份读进内存再判上限，峰值已经发生
        了（300MB 的成片、两个并发就是 600MB 常驻进程）。
        """
        size = 0
        handle = await asyncio.to_thread(target.open, "wb")
        try:
            async for chunk in self.storage.iter_chunks(key):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise LibraryFileTooLarge(details={"maxBytes": MAX_BYTES})
                await asyncio.to_thread(handle.write, chunk)
        finally:
            await asyncio.to_thread(handle.close)
        return size

    async def _assert_own_key(self, key: str, owner_id: str) -> None:
        """HTTP 入参给的存储键必须指向**调用方自己的**素材行。

        不校验的话，`POST /assets/from-generation` 就是一个「任意对象读取」
        原语：主存储里装的是**所有人**的私有原片，知道 key 就能把别人的字节
        读成自己名下的素材，随后 `GET /assets/{id}/file` 就把它拿走了。

        这里只认「自己名下的资产行」（原片键或封面键）：库里没有对应行的键
        （别的模块的产物、生成侧的中间文件）同样不能读——那正是「猜到 key 就
        能拿走」的另一半。
        """
        owned = await self.session.scalar(
            select(LibraryAsset.id)
            .where(
                or_(
                    and_(
                        LibraryAsset.storage_key == key,
                        LibraryAsset.owner_id == owner_id,
                    ),
                    and_(
                        LibraryAsset.cover_key == key,
                        LibraryAsset.owner_id == owner_id,
                    ),
                )
            )
            .limit(1)
        )
        if owned is None:
            raise IngestKeyNotOwned(details={"storageKey": key})

    async def _by_source_key(self, source_key: str, owner_id: str) -> LibraryAsset | None:
        """幂等查找。**命中但 owner 不一致时 409**，不返回别人的行。

        `uq_library_assets_source_key` 是**全局**唯一：只按 key 查的话，B 用
        「A 的幂等键」入库会命中 A 的行——B 的字节从未入库，返回的却是 A 的
        资产 id（还是 200 + created=false，调用方会以为已经存好了）。

        宁可让调用方换一个 key，也不要把别人的行当成「已经入过库」。上传侧早就
        踩过这个坑并修过（`service.upload` 给键加 `upload:<actorId>:` 前缀），
        入库侧这次补上同一条口径。
        """
        row = await self.session.scalar(
            select(LibraryAsset).where(LibraryAsset.source_key == source_key)
        )
        if row is None:
            return None
        if row.owner_id != owner_id:
            raise Conflict(
                "该幂等键已被其他用户占用，请换一个 sourceKey",
                details={"sourceKey": source_key},
            )
        return row

    async def _replace(
        self, asset_id: str, *, owner_id: str, values: dict[str, Any]
    ) -> LibraryAsset | None:
        """把已有资产原地换成新内容；找不到 / 不是本人的 / 已删除时返回 None。

        返回 None 时调用方退回「正常新建」——宁可多一条资产，也不要因为替换失败
        把用户去字幕的结果吞掉。
        """
        row = await self.session.get(LibraryAsset, asset_id)
        if row is None or row.deleted_at is not None or row.owner_id != owner_id:
            logger.warning("要替换的资产不可用，退回新建 asset=%s owner=%s", asset_id, owner_id)
            return None
        for column, value in values.items():
            setattr(row, column, value)
        await self.session.flush()
        return row

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
        # 端口调用方是服务端模块（生成流水线 / 模板替换）：它给的键是它自己刚
        # 写进去的，归属由它负责，所以这里是 trusted；HTTP 端点不是。
        return await GenerationIngest(session, get_storage()).run(request, trusted=True)


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
