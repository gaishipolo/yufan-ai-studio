"""素材权限、登记和流式上传编排；所有磁盘操作在线程中执行。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, Forbidden, NotFound
from core.logging import get_logger
from core.media import MediaCategory, StoredMedia
from infra.archive import ArchiveStorage, get_archive_storage, within
from infra.media_catalog import catalog_lease
from infra.media_probe import MAX_BYTES, PREVIEW_VERSION, create_browser_preview, create_preview
from modules.asset.archive_repository import ArchiveAssetRepository
from modules.asset.archive_schema import (
    ArchiveCategory,
    ArchiveChange,
    ArchivedAsset,
    ArchiveList,
    ArchiveScope,
    ArchiveState,
    ImportAsset,
)

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

    def output(self, media: StoredMedia, owner: str) -> ArchivedAsset:
        item = ArchivedAsset.model_validate(media.model_dump())
        item.editable = media.owner_id == owner
        item.download_allowed = item.editable
        if not item.editable:
            # 非本人素材只回预览地址；原件路径与摘要不外露
            # （`ArchivedAsset` 本身不含 `preview_path`，预览走 `/preview` 接口）。
            item.path = ""
            item.sha256 = ""
            item.url = f"/api/v1/assets/archive/{media.id}/preview"
        return item

    def list_assets(
        self,
        owner: str,
        category: ArchiveCategory,
        scope: ArchiveScope = "mine",
        keyword: str = "",
        state: ArchiveState = "all",
        page: int = 1,
        page_size: int = 200,
    ) -> ArchiveList:
        rows = []
        for media in self.repo.list():
            visible = (
                (media.owner_id == owner and bool(media.deleted_at))
                if scope == "trash"
                else (
                    not media.deleted_at
                    and (
                        media.owner_id == owner
                        if scope == "mine"
                        else media.category == "source" and media.shared
                    )
                )
            )
            if (
                visible
                and (category == "all" or media.category == category)
                and (keyword.strip().casefold() in media.name.casefold())
                and (state == "all" or media.shared == (state == "shared"))
            ):
                rows.append(media)
        rows.sort(key=lambda m: (m.created_at, m.id), reverse=True)
        return ArchiveList(
            root=str(self.storage.root()) if scope != "public" else "",
            items=[self.output(m, owner) for m in rows[(page - 1) * page_size : page * page_size]],
            total=len(rows),
            used_bytes=sum(m.size_bytes for m in rows),
            page=page,
            page_size=page_size,
        )

    def change_action(
        self, uid: str, owner: str, payload: ArchiveChange, field: str
    ) -> ArchivedAsset:
        values = payload.model_dump(exclude_none=True)
        if set(values) != {"expected_version", field}:
            raise BadRequest("请仅提交当前操作对应的字段")
        return self.change(uid, owner, payload)

    async def upload_for(
        self,
        chunks: AsyncIterator[bytes],
        filename: str,
        category: MediaCategory,
        actor: CurrentUser,
    ) -> ArchivedAsset:
        if category != "source" and not actor.has("asset:reference:manage"):
            raise Forbidden("参考素材上传需要素材专员权限")
        return await self.upload(chunks, filename, category, actor.id)

    def import_for(self, payload: ImportAsset, actor: CurrentUser) -> ArchivedAsset:
        if not actor.has("asset:reference:manage"):
            raise Forbidden("inbox 登记需要素材专员权限")
        return self.import_asset(payload, actor.id)

    def change(self, asset_id: str, owner: str, payload: ArchiveChange) -> ArchivedAsset:
        with catalog_lease(self.storage):
            media = self.owned(asset_id, owner)
            if payload.expected_version != media.state_version:
                # 响应丢失后的相同状态重试只返回事实，不再次改写时间和版本。
                same = (
                    (payload.name is None or payload.name.strip() == media.name)
                    and (payload.shared is None or payload.shared == media.shared)
                    and (payload.deleted is None or payload.deleted == bool(media.deleted_at))
                )
                if same:
                    return self.output(media, owner)
                raise Conflict("素材状态已变化，请刷新后重新操作")
            if payload.name is not None:
                if not payload.name.strip():
                    raise BadRequest("名称不能为空")
                media.name = payload.name.strip()
            if payload.shared is not None:
                if media.category != "source" or media.deleted_at:
                    raise BadRequest("只能分享未删除的本人原片")
                media.shared = payload.shared
                media.shared_at = datetime.now(UTC) if payload.shared else None
            if payload.deleted is not None:
                media.deleted_at = datetime.now(UTC) if payload.deleted else None
                media.shared = False
                media.shared_at = None
            media.state_version += 1
            self.repo.save(media)
            return self.output(media, owner)

    def visible(self, asset_id: str, owner: str) -> StoredMedia:
        media = self.repo.get(asset_id)
        if media.deleted_at or (
            media.owner_id != owner and not (media.category == "source" and media.shared)
        ):
            raise NotFound("素材不存在或已停止共享")
        return media

    def detail(self, asset_id: str, owner: str) -> ArchivedAsset:
        return self.output(self.visible(asset_id, owner), owner)

    def preview(self, asset_id: str, owner: str) -> tuple[Path, str]:
        media = self.visible(asset_id, owner)
        if media.type == "video":
            return self.stream(asset_id, owner)
        return self.storage.resolve(media.path), media.mime_type

    def import_asset(self, payload: ImportAsset, owner: str) -> ArchivedAsset:
        source = within(self.storage.root() / "inbox", payload.relative_path)
        if not source.is_file():
            raise BadRequest("请先将文件放入 Z 盘归档根目录的 inbox 文件夹")
        media = self.repo.register(source, source.name, payload.category, owner)
        return ArchivedAsset.model_validate(media.model_dump())

    def file(self, asset_id: str, owner: str, *, preview: bool = False) -> tuple[Path, str]:
        media = self.visible(asset_id, owner) if preview else self.owned(asset_id, owner)
        if media.deleted_at:
            raise NotFound("素材已移入回收站")
        if preview and media.type == "video":
            derived = self.storage.resolve(f"library/previews/{media.id}.{PREVIEW_VERSION}.jpg")
            if create_preview(self.storage.resolve(media.path), derived, media.type):
                return derived, "image/jpeg"
        key = media.preview_path if preview else media.path
        if not key:
            raise NotFound("此素材没有缩略图")
        return self.storage.resolve(key), "image/jpeg" if preview else media.mime_type

    def stream(self, asset_id: str, owner: str) -> tuple[Path, str]:
        """悬停/页面预览用的视频流；原片字节始终由 `file` 提供，不混用。"""
        media = self.visible(asset_id, owner)
        if media.type != "video":
            raise NotFound("此素材没有视频预览")
        original = self.storage.resolve(media.path)
        derived = self.storage.resolve(f"library/previews/{media.id}.{PREVIEW_VERSION}.mp4")
        if derived.is_file():
            return derived, "video/mp4"
        if create_browser_preview(original, derived):
            return derived, "video/mp4"
        if media.owner_id != owner:
            raise BadRequest("公共预览生成失败，请稍后重试；原件不会作为预览返回")
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
