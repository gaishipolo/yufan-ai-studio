"""归档素材编排：登记、列表、取文件；索引在 DB，字节在归档盘。

## 与 `AssetService` 的分工

两个 service 共用 `assets` 表，但用的是两套**物理存储**：

- `AssetService`：对象存储（本地 `STORAGE_LOCAL_ROOT` / S3），服务端中转上传，
  缩略图按需派生，文件走 `FileResponse` 或预签名跳转。
- 本文件：归档盘（`MEDIA_ARCHIVE_ROOT`，通常是 Z 盘/共享盘），原片直接落盘，
  300MB 上限，供替换模板的 `prepare` 读原始视频。

所以两边的 `storage_key` 不是同一个命名空间，靠 `source` 列区分。
列表接口也各自限定来源，不会把对方的行渲染成 404 的封面。

## 三个可见性层级

| 谁在问 | 能看什么 | 入口 |
|---|---|---|
| 本人 | 自己的全部（含回收站、未分享） | `owned()` |
| 任何人 | 未删除、且「自己的」或「别人已分享的原片」 | `visible()` |
| 列表 | 按 `scope` 分池：mine / public / trash | `list_visible()` |

`owned()` 与 `visible()` 不能合并：删除、改名只能动自己的记录，
而预览、详情要放行公共原片。合在一起就得在每个调用点补 if。

## 为什么仍然写 JSON

替换模板在 worker 里同步读 `catalog/assets/{id}.json`。为了让「替换模板零改动」
成立，入库时同步落一份 JSON 镜像（内容由 DB 行派生）。写失败即整次入库失败：
「列表里有、prepare 说没有」是最难排查的一种不一致。

改名/分享/移入回收站之后也要重写镜像 —— 否则模板侧读到的还是「可引用」的旧状态。

## 列表不依赖归档盘在线

列表读 DB，`root` 取不到时返回空串而不是 502 —— 盘掉线时运维仍需要看到
「库里有多少素材、占多少空间」。取文件、上传、导入仍然要求盘可用。
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, Forbidden, NotFound, UpstreamError
from core.logging import get_logger
from core.media import MediaCategory
from infra.archive import (
    ArchiveStorage,
    BlobStore,
    clip_key,
    get_archive_storage,
    preview_key,
    within,
)
from infra.media_probe import (
    EXTENSIONS,
    MAX_BYTES,
)
from infra.media_probe import (
    inspect as probe_media,
)
from modules.asset.archive_repository import CATEGORIES, ArchiveAssetRepository
from modules.asset.archive_schema import (
    ArchiveCategory,
    ArchiveChange,
    ArchivedAsset,
    ArchiveList,
    ArchiveScope,
    ArchiveState,
    ImportAsset,
)
from modules.asset.models import Asset
from modules.asset.schema import AssetSource

logger = get_logger(__name__)


class ArchiveAssetService:
    def __init__(self, session: AsyncSession, storage: ArchiveStorage | None = None) -> None:
        self.session = session
        self.storage = storage or get_archive_storage()
        self.blobs = BlobStore(self.storage)
        self.repo = ArchiveAssetRepository(session)

    # ------------------------------------------------------------------ 读
    async def owned(self, asset_id: str, owner: str) -> Asset:
        """本人记录：回收站里的也要能取（恢复操作要读它）。"""
        row = await self.repo.get(asset_id)
        if row.owner_id != owner:
            raise NotFound("素材不存在")
        return row

    async def visible(self, asset_id: str, owner: str) -> Asset:
        """公共可见：本人未删除的，或别人已分享的未删除原片。"""
        row = await self.repo.get(asset_id)
        shared_source = row.category == "source" and row.shared
        if row.deleted_at is not None or (row.owner_id != owner and not shared_source):
            raise NotFound("素材不存在或已停止共享")
        return row

    async def list_assets(
        self,
        owner: str,
        category: ArchiveCategory = "all",
        scope: ArchiveScope = "mine",
        keyword: str = "",
        state: ArchiveState = "all",
        page: int = 1,
        page_size: int = 200,
    ) -> ArchiveList:
        if category not in (*CATEGORIES, "all"):
            raise BadRequest("素材分类无效")
        rows, total, used_bytes = await self.repo.list_visible(
            owner, category, scope, keyword, state, page, page_size
        )
        return ArchiveList(
            # 公共池不暴露归档根路径：调用方不需要知道盘符，也没有权限访问
            root="" if scope == "public" else self._root(),
            items=[self._to_out(row, owner) for row in rows],
            total=total,
            used_bytes=used_bytes,
            page=page,
            page_size=page_size,
        )

    async def detail(self, asset_id: str, owner: str) -> ArchivedAsset:
        """详情：本人读自己的任何状态（含回收站，否则没法拿版本号去恢复），
        别人只能读已分享且未删除的原片。"""
        row = await self.repo.get(asset_id)
        if row.owner_id != owner and (
            row.deleted_at is not None or not (row.category == "source" and row.shared)
        ):
            raise NotFound("素材不存在或已停止共享")
        return self._to_out(row, owner)

    async def preview(self, asset_id: str, owner: str) -> tuple[Path, str]:
        """受控公共预览：图片给原图，视频给浏览器可播的派生流。"""
        row = await self.visible(asset_id, owner)
        if row.type == "video":
            return await self.stream(asset_id, owner)
        return await asyncio.to_thread(self._materialized, row.storage_key), row.mime_type

    async def file(self, asset_id: str, owner: str, *, preview: bool = False) -> tuple[Path, str]:
        row = await self.visible(asset_id, owner) if preview else await self.owned(asset_id, owner)
        if not preview and row.deleted_at is not None:
            raise NotFound("素材已移入回收站")
        if preview and row.type == "video" and not row.cover_key:
            # 视频没有派生封面时现场截一帧：列表用 `<img>` 取缩略图，喂不了 mp4
            derived = preview_key(_require_digest(row))
            if await asyncio.to_thread(self._create_preview, row.storage_key, derived, row.type):
                return await asyncio.to_thread(self._materialized, derived), "image/jpeg"
        key = row.cover_key if preview else row.storage_key
        if not key:
            raise NotFound("此素材没有缩略图")
        return await asyncio.to_thread(self._materialized, key), (
            "image/jpeg" if preview else row.mime_type
        )

    async def stream(self, asset_id: str, owner: str) -> tuple[Path, str]:
        """悬停/页面预览用的视频流；原片字节始终由 `file` 提供，不混用。"""
        row = await self.visible(asset_id, owner)
        if row.type != "video":
            raise NotFound("此素材没有视频预览")
        derived = clip_key(_require_digest(row))
        if await asyncio.to_thread(self._derive_clip, row.storage_key, derived):
            return await asyncio.to_thread(self._materialized, derived), "video/mp4"
        if row.owner_id != owner:
            # 别人的原片不能当预览回出去：派生失败就是失败，不能退化成原片
            raise BadRequest("公共预览生成失败，请稍后重试；原件不会作为预览返回")
        logger.warning("browser preview failed, fallback to original: %s", asset_id)
        return await asyncio.to_thread(self._materialized, row.storage_key), row.mime_type

    # ------------------------------------------------------------------ 写
    async def import_asset(self, payload: ImportAsset, owner: str) -> ArchivedAsset:
        source = await asyncio.to_thread(
            within, await asyncio.to_thread(self.storage.root) / "inbox", payload.relative_path
        )
        if not source.is_file():
            raise BadRequest("请先将文件放入 Z 盘归档根目录的 inbox 文件夹")
        return await self.register(source, source.name, payload.category, owner)

    async def import_for(self, payload: ImportAsset, actor: CurrentUser) -> ArchivedAsset:
        if not actor.has("asset:reference:manage"):
            raise Forbidden("inbox 登记需要素材专员权限")
        return await self.import_asset(payload, actor.id)

    async def upload(
        self,
        chunks: AsyncIterator[bytes],
        filename: str,
        category: MediaCategory,
        owner: str,
    ) -> ArchivedAsset:
        temporary = await asyncio.to_thread(self._temporary)
        try:
            await self._receive(temporary, chunks)
            return await self.register(temporary, filename, category, owner)
        finally:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)

    async def upload_for(
        self,
        chunks: AsyncIterator[bytes],
        filename: str,
        category: MediaCategory,
        actor: CurrentUser,
    ) -> ArchivedAsset:
        # 原片谁都能传（替换任务的输入就是原片），人物/产品/场景/音频属于受管参考素材
        if category != "source" and not actor.has("asset:reference:manage"):
            raise Forbidden("参考素材上传需要素材专员权限")
        return await self.upload(chunks, filename, category, actor.id)

    async def register(
        self, source: Path, filename: str, category: MediaCategory, owner: str
    ) -> ArchivedAsset:
        """登记一份**已经在盘上**的文件（上传的临时文件、inbox 里的文件、迁移脚本）。

        复制进归档目录、派生缩略图、落 DB 行、写 JSON 镜像，四件事要么全成要么全不成。
        """
        return await self._ingest(source, filename, category, owner)

    async def change_action(
        self, asset_id: str, owner: str, payload: ArchiveChange, field: str
    ) -> ArchivedAsset:
        """三个 PUT 共用一条写路径；`field` 声明这一次允许改哪个字段。"""
        values = payload.model_dump(exclude_none=True)
        if set(values) != {"expected_version", field}:
            raise BadRequest("请仅提交当前操作对应的字段")
        return await self.change(asset_id, owner, payload)

    async def change(self, asset_id: str, owner: str, payload: ArchiveChange) -> ArchivedAsset:
        """改名 / 分享 / 移入回收站。

        并发保护靠 `state_version` 乐观锁，不用文件锁：索引在 DB，一次更新就是一个
        事务；归档盘上的 JSON 只是镜像，不需要跨进程串行化。
        """
        row = await self.owned(asset_id, owner)
        if payload.expected_version != row.state_version:
            # 响应丢失后的相同状态重试只返回事实，不再次改写时间和版本
            same = (
                (payload.name is None or payload.name.strip() == row.name)
                and (payload.shared is None or payload.shared == row.shared)
                and (payload.deleted is None or payload.deleted == (row.deleted_at is not None))
            )
            if same:
                return self._to_out(row, owner)
            raise Conflict("素材状态已变化，请刷新后重新操作")
        if payload.name is not None:
            if not payload.name.strip():
                raise BadRequest("名称不能为空")
            row.name = payload.name.strip()[:200]
        if payload.shared is not None:
            if row.category != "source" or row.deleted_at is not None:
                raise BadRequest("只能分享未删除的本人原片")
            row.shared = payload.shared
            row.shared_at = datetime.now(UTC) if payload.shared else None
        if payload.deleted is not None:
            row.deleted_at = datetime.now(UTC) if payload.deleted else None
            # 移入回收站即撤回分享：留着 shared 会让它继续出现在公共池
            row.shared = False
            row.shared_at = None
        row.state_version += 1
        await self.repo.save(row)
        # 镜像必须在 commit 前写好：模板侧读 JSON 判断「还能不能用」，
        # 只改库不改 JSON，会出现「列表已删、模板还能引用」的错位。
        await self._write_mirror(row)
        await self.session.commit()
        return self._to_out(row, owner)

    # ------------------------------------------------------------------ 内部
    async def _ingest(
        self, source: Path, filename: str, category: MediaCategory, owner: str
    ) -> ArchivedAsset:
        ext = Path(filename).suffix.lower()
        info = await asyncio.to_thread(probe_media, source, ext)
        mime = EXTENSIONS[ext]
        kind = _kind_of(mime)
        _ensure_category(category, kind)

        digest, size_bytes = await asyncio.to_thread(_digest_and_size, source)
        existing = await self._find_by_digest(owner, digest, category)
        if existing is not None:
            # 同一个人把同一份文件重复登记到**同一个分类**：返回已有记录，
            # 连对象都不再复制一份。分类不同则各自一条 —— 一张图同时当人物参考
            # 和产品参考是正常用法，不该被"内容相同"合并掉。
            return self._to_out(existing, owner)

        uid = uuid.uuid4().hex
        # 字节按 sha256 落库（`blobs/ab/abcdef…`）：位置由内容决定，不由登记时间决定。
        # 于是同一份文件被两个人传、或同一人传到两个分类，物理上只有一份；
        # 而"这份字节还是不是当初那份"也不再需要每次读出来重算——key 就是答案。
        key, created = await asyncio.to_thread(self.blobs.ensure, source, digest, ext)
        cover_key = preview_key(digest)

        has_preview = await asyncio.to_thread(self._create_preview, key, cover_key, kind)
        if not has_preview:
            logger.info("归档素材没有缩略图（音轨或派生失败）：%s", uid)

        row = Asset(
            id=uid,
            name=Path(filename).name[:200],
            type=kind,
            mime_type=mime,
            size_bytes=size_bytes,
            storage_key=key,
            cover_key=cover_key if has_preview else "",
            sha256=digest,
            category=category,
            source=AssetSource.ARCHIVE,
            owner_id=owner,
            created_at=datetime.now(UTC),
            width=info["width"],
            height=info["height"],
            duration=info["duration"],
            has_audio=info["hasAudio"],
        )
        try:
            await self._write_mirror(row)
            await self.repo.add(row)
            await self.session.commit()
        except IntegrityError:
            # 并发登记同一份文件：部分唯一索引兜底（归档侧不参与该索引，实际很难触发，
            # 但索引不是这条代码路径的替代品）。回滚后按摘要取已有记录。
            await self.session.rollback()
            await asyncio.to_thread(self._discard_blob, key, created)
            await asyncio.to_thread(self._discard_mirror, uid)
            existing = await self._find_by_digest(owner, digest, category)
            if existing is not None:
                return self._to_out(existing, owner)
            raise
        except Exception:
            await self.session.rollback()
            await asyncio.to_thread(self._discard_blob, key, created)
            await asyncio.to_thread(self._discard_mirror, uid)
            raise
        return self._to_out(row, owner)

    async def _find_by_digest(self, owner: str, digest: str, category: str) -> Asset | None:
        stmt = select(Asset).where(
            Asset.owner_id == owner,
            Asset.sha256 == digest,
            Asset.category == category,
            Asset.source == AssetSource.ARCHIVE,
        )
        return await self.session.scalar(stmt.limit(1))

    async def _receive(self, target: Path, chunks: AsyncIterator[bytes]) -> None:
        """把请求体流式落盘：300MB 原片不进内存。"""
        total = 0
        stream = await asyncio.to_thread(target.open, "xb")
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_BYTES:
                    raise BadRequest("文件不能超过 300MB")
                await asyncio.to_thread(stream.write, chunk)
        finally:
            await asyncio.to_thread(stream.close)
        if total == 0:
            raise BadRequest("文件不能为空")

    def _materialized(self, key: str) -> Path:
        """取出可读路径。

        上下文在这里就退出了，所以**依赖驱动承诺的「退出后缓存仍可读」**：
        本地驱动交出的就是归档里的原路径，远端驱动把字节留在物化缓存里等淘汰。
        不这样做的话，`FileResponse` 拿到的路径会在响应发出前失效。
        """
        with self.blobs.materialize(key) as path:
            return path

    def _create_preview(self, key: str, cover_key: str, kind: str) -> bool:
        """派生静帧封面（图片缩放 / 视频截帧）。"""
        return self.blobs.derive(key, cover_key, kind=kind)

    def _derive_clip(self, key: str, target_key: str) -> bool:
        """派生浏览器可播的转码流。"""
        return self.blobs.derive(key, target_key, kind="video", clip=True)

    async def _write_mirror(self, row: Asset) -> None:
        payload = self.repo.to_media(row).model_dump(mode="json", by_alias=True)
        await asyncio.to_thread(self.storage.write, f"catalog/assets/{row.id}.json", payload)

    def _discard_mirror(self, asset_id: str) -> None:
        self.storage.remove(f"catalog/assets/{asset_id}.json")

    def _discard_blob(self, key: str, created: bool) -> None:
        """登记失败不留半条记录，但**也不删字节**。

        内容寻址下同一个 key 可能正被别的记录共享（同一份文件被另一个人登记、
        或同一人登记到了另一个分类），删它等于打断别人。留一份没人引用的字节
        只是磁盘占用，`app prune-blobs` 能扫出来；删错则是数据丢失。
        """
        if created:
            logger.info("登记失败，已写入的字节留待回收：%s", key)

    def _temporary(self) -> Path:
        """上传中转文件放本机临时目录，不再借用归档盘。

        归档盘可能是只读挂载或远端桶，而中转文件只在本进程内活几秒；
        放本机也能让 300MB 的流式落盘不等网络。
        """
        folder = Path(tempfile.gettempdir()) / "yufan-archive"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / (uuid.uuid4().hex + ".upload")

    def _to_out(self, row: Asset, owner: str) -> ArchivedAsset:
        """DB 行 → 出参，并**按调用者裁剪**。

        别人的素材只回预览地址：原件路径（Z 盘相对 key）与摘要不外露，
        否则公共池等于把归档盘目录结构发给了所有账号。
        """
        item = ArchivedAsset.model_validate(self.repo.to_media(row).model_dump())
        item.editable = row.owner_id == owner
        item.download_allowed = item.editable
        if not item.editable:
            item.path = ""
            item.sha256 = ""
            item.url = f"{settings.api_prefix}/assets/archive/{row.id}/preview"
        return item

    def _root(self) -> str:
        try:
            return str(self.storage.root())
        except UpstreamError:
            # 盘掉线不等于库不可读：列表仍要给运维可用信息，见模块 docstring
            return ""


def _require_digest(row: Asset) -> str:
    """派生 key 由摘要决定：缺摘要就没法定位字节，也没法定位它的缓存。"""
    if not row.sha256:
        raise NotFound("素材摘要缺失，请重新登记")
    return row.sha256


def _digest_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _kind_of(mime: str) -> str:
    if mime.startswith("video/"):
        return "video"
    return "audio" if mime.startswith("audio/") else "image"


def _ensure_category(category: MediaCategory, kind: str) -> None:
    """分类与媒体类型必须匹配：人物库里混进一段音频，替换模板会在运行时炸。"""
    expected = {
        "source": "video",
        "character": "image",
        "product": "image",
        "scene": "image",
        "audio": "audio",
    }[category]
    if kind != expected:
        raise BadRequest("原片需为 MP4，人物/产品/场景需为图片，音频分类需为音频")
