"""素材权限、登记和流式上传编排；所有磁盘操作在线程中执行。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from core.exceptions import BadRequest, NotFound
from core.logging import get_logger
from core.media import MediaCategory, StoredMedia
from infra.media_probe import MAX_BYTES, create_browser_preview
from infra.storage.archive import ArchiveStorage, get_archive_storage, within
from modules.asset.archive_repository import CATEGORIES, ArchiveAssetRepository
from modules.asset.archive_schema import ArchivedAsset, ArchiveList, ImportAsset

logger = get_logger(__name__)


class ArchiveAssetService:
    def __init__(self, storage: ArchiveStorage | None = None) -> None:
        self.storage = storage or get_archive_storage()
        self.repo = ArchiveAssetRepository(self.storage)

    def owned(self, asset_id: str, owner: str) -> StoredMedia:
        media = self.repo.get(asset_id)
        if media.owner_id != owner:
            raise NotFound("素材不存在")
        return media

    def list_assets(self, owner: str, category: str) -> ArchiveList:
        if category not in (*CATEGORIES, "all"):
            raise BadRequest("素材分类无效")
        rows = sorted(
            (
                m
                for m in self.repo.list()
                if m.owner_id == owner and (category == "all" or m.category == category)
            ),
            key=lambda m: m.created_at,
            reverse=True,
        )
        return ArchiveList(
            root=str(self.storage.root()),
            items=[ArchivedAsset.model_validate(m.model_dump()) for m in rows],
            total=len(rows),
            used_bytes=sum(m.size_bytes for m in rows),
        )

    def import_asset(self, payload: ImportAsset, owner: str) -> ArchivedAsset:
        source = within(self.storage.root() / "inbox", payload.relative_path)
        if not source.is_file():
            raise BadRequest("请先将文件放入 Z 盘归档根目录的 inbox 文件夹")
        media = self.repo.register(source, source.name, payload.category, owner)
        return ArchivedAsset.model_validate(media.model_dump())

    def file(self, asset_id: str, owner: str, *, preview: bool = False) -> tuple[Path, str]:
        media = self.owned(asset_id, owner)
        key = media.preview_path if preview else media.path
        if not key:
            raise NotFound("此素材没有缩略图")
        return self.storage.resolve(key), "image/jpeg" if preview else media.mime_type

    def stream(self, asset_id: str, owner: str) -> tuple[Path, str]:
        """悬停/页面预览用的视频流；原片字节始终由 `file` 提供，不混用。"""
        media = self.owned(asset_id, owner)
        if media.type != "video":
            raise NotFound("此素材没有视频预览")
        original = self.storage.resolve(media.path)
        derived = self.storage.resolve(f"library/previews/{media.id}.mp4")
        if derived.is_file():
            return derived, "video/mp4"
        if create_browser_preview(original, derived):
            return derived, "video/mp4"
        logger.warning("browser preview failed, fallback to original: %s", asset_id)
        return original, media.mime_type

    async def upload(
        self,
        chunks: AsyncIterator[bytes],
        filename: str,
        category: MediaCategory,
        owner: str,
    ) -> ArchivedAsset:
        temporary = await asyncio.to_thread(self._temporary)
        total = 0
        try:
            stream = await asyncio.to_thread(temporary.open, "xb")
            try:
                async for chunk in chunks:
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise BadRequest("文件不能超过 300MB")
                    await asyncio.to_thread(stream.write, chunk)
            finally:
                await asyncio.to_thread(stream.close)
            media = await asyncio.to_thread(
                self.repo.register, temporary, filename, category, owner
            )
            return ArchivedAsset.model_validate(media.model_dump())
        finally:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)

    def _temporary(self) -> Path:
        folder = self.storage.resolve("incoming")
        folder.mkdir(exist_ok=True)
        return folder / (uuid.uuid4().hex + ".upload")
