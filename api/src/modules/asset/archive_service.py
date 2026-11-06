"""归档素材编排：登记、列表、取文件；索引在 DB，字节在归档盘。

## 与 `AssetService` 的分工

两个 service 共用 `assets` 表，但用的是两套**物理存储**：

- `AssetService`：对象存储（本地 `STORAGE_LOCAL_ROOT` / S3），服务端中转上传，
  缩略图按需派生，文件走 `FileResponse` 或预签名跳转。
- 本文件：归档盘（`MEDIA_ARCHIVE_ROOT`，通常是 Z 盘/共享盘），原片直接落盘，
  300MB 上限，供替换模板的 `prepare` 读原始视频。

所以两边的 `storage_key` 不是同一个命名空间，靠 `source` 列区分。
列表接口也各自限定来源，不会把对方的行渲染成 404 的封面。

## 为什么仍然写 JSON

替换模板在 worker 里同步读 `catalog/assets/{id}.json`。为了让「替换模板零改动」
成立，入库时同步落一份 JSON 镜像（内容由 DB 行派生）。写失败即整次入库失败：
「列表里有、prepare 说没有」是最难排查的一种不一致。

## 列表不依赖归档盘在线

列表读 DB，`root` 取不到时返回空串而不是 502 —— 盘掉线时运维仍需要看到
「库里有多少素材、占多少空间」。取文件、上传、导入仍然要求盘可用。
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import BadRequest, NotFound, UpstreamError
from core.logging import get_logger
from core.media import MediaCategory
from infra.archive import ArchiveStorage, get_archive_storage, within
from infra.media_probe import (
    EXTENSIONS,
    MAX_BYTES,
    create_browser_preview,
    create_preview,
)
from infra.media_probe import (
    inspect as probe_media,
)
from modules.asset.archive_repository import CATEGORIES, ArchiveAssetRepository
from modules.asset.archive_schema import ArchivedAsset, ArchiveList, ImportAsset
from modules.asset.models import Asset
from modules.asset.schema import AssetSource

logger = get_logger(__name__)

# 归档素材的时区：历史 JSON 按北京时间切月份目录，导入时保持同一约定
_LOCAL_TZ = timezone(timedelta(hours=8))


class ArchiveAssetService:
    def __init__(
        self, session: AsyncSession, storage: ArchiveStorage | None = None
    ) -> None:
        self.session = session
        self.storage = storage or get_archive_storage()
        self.repo = ArchiveAssetRepository(session)

    # ------------------------------------------------------------------ 读
    async def owned(self, asset_id: str, owner: str) -> Asset:
        row = await self.repo.get(asset_id)
        if row.owner_id != owner:
            raise NotFound("素材不存在")
        return row

    async def list_assets(self, owner: str, category: str) -> ArchiveList:
        if category not in (*CATEGORIES, "all"):
            raise BadRequest("素材分类无效")
        rows = await self.repo.list(owner, category)
        return ArchiveList(
            root=self._root(),
            items=[self._to_out(row) for row in rows],
            total=len(rows),
            used_bytes=sum(row.size_bytes for row in rows),
        )

    async def file(self, asset_id: str, owner: str, *, preview: bool = False) -> tuple[Path, str]:
        row = await self.owned(asset_id, owner)
        key = row.cover_key if preview else row.storage_key
        if not key:
            raise NotFound("此素材没有缩略图")
        return await asyncio.to_thread(self.storage.resolve, key), (
            "image/jpeg" if preview else row.mime_type
        )

    async def stream(self, asset_id: str, owner: str) -> tuple[Path, str]:
        """悬停/页面预览用的视频流；原片字节始终由 `file` 提供，不混用。"""
        row = await self.owned(asset_id, owner)
        if row.type != "video":
            raise NotFound("此素材没有视频预览")
        original = await asyncio.to_thread(self.storage.resolve, row.storage_key)
        derived = await asyncio.to_thread(
            self.storage.resolve, f"library/previews/{row.id}.mp4"
        )
        if derived.is_file() or await asyncio.to_thread(
            create_browser_preview, original, derived
        ):
            return derived, "video/mp4"
        logger.warning("browser preview failed, fallback to original: %s", asset_id)
        return original, row.mime_type

    # ------------------------------------------------------------------ 写
    async def import_asset(self, payload: ImportAsset, owner: str) -> ArchivedAsset:
        source = await asyncio.to_thread(
            within, await asyncio.to_thread(self.storage.root) / "inbox", payload.relative_path
        )
        if not source.is_file():
            raise BadRequest("请先将文件放入 Z 盘归档根目录的 inbox 文件夹")
        return await self.register(source, source.name, payload.category, owner)

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

    async def register(
        self, source: Path, filename: str, category: MediaCategory, owner: str
    ) -> ArchivedAsset:
        """登记一份**已经在盘上**的文件（上传的临时文件、inbox 里的文件、迁移脚本）。

        复制进归档目录、派生缩略图、落 DB 行、写 JSON 镜像，四件事要么全成要么全不成。
        """
        return await self._ingest(source, filename, category, owner)

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
            return self._to_out(existing)

        uid = uuid.uuid4().hex
        key = f"library/{category}/{datetime.now(_LOCAL_TZ):%Y/%m}/{uid}{ext}"
        cover_key = f"library/previews/{uid}.jpg"

        await asyncio.to_thread(self._copy, source, key, digest)
        has_preview = await asyncio.to_thread(
            create_preview, self.storage.resolve(key), self.storage.resolve(cover_key), kind
        )
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
            await self._discard(key, cover_key)
            await asyncio.to_thread(self._discard_mirror, uid)
            existing = await self._find_by_digest(owner, digest, category)
            if existing is not None:
                return self._to_out(existing)
            raise
        except Exception:
            await self.session.rollback()
            await self._discard(key, cover_key)
            await asyncio.to_thread(self._discard_mirror, uid)
            raise
        return self._to_out(row)

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

    def _copy(self, source: Path, key: str, digest: str) -> None:
        self.storage.copy(source, self.storage.resolve(key), digest)

    async def _write_mirror(self, row: Asset) -> None:
        payload = self.repo.to_media(row).model_dump(mode="json", by_alias=True)
        await asyncio.to_thread(
            self.storage.write, self.storage.resolve(f"catalog/assets/{row.id}.json"), payload
        )

    def _discard_mirror(self, asset_id: str) -> None:
        path = self.storage.resolve(f"catalog/assets/{asset_id}.json")
        path.unlink(missing_ok=True)

    async def _discard(self, *keys: str) -> None:
        for key in keys:
            if not key:
                continue

            def remove(target: str = key) -> None:
                path = self.storage.resolve(target)
                path.unlink(missing_ok=True)

            try:
                await asyncio.to_thread(remove)
            except OSError:  # pragma: no cover - 清理失败不影响主流程
                logger.warning("回滚已写入的归档对象失败：%s", key)

    def _temporary(self) -> Path:
        folder = self.storage.resolve("incoming")
        folder.mkdir(exist_ok=True)
        return folder / (uuid.uuid4().hex + ".upload")

    def _to_out(self, row: Asset) -> ArchivedAsset:
        return ArchivedAsset.model_validate(self.repo.to_media(row).model_dump())

    def _root(self) -> str:
        try:
            return str(self.storage.root())
        except UpstreamError:
            # 盘掉线不等于库不可读：列表仍要给运维可用信息，见模块 docstring
            return ""


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
