"""归档素材索引仓储，保留已迁移的 JSON 文件布局。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from core.exceptions import BadRequest, NotFound
from core.media import MediaCategory, StoredMedia
from infra.media_probe import EXTENSIONS, create_preview, inspect
from infra.storage.archive import ArchiveStorage, get_archive_storage

CATEGORIES = ("source", "character", "product", "scene", "audio")


class ArchiveAssetRepository:
    def __init__(self, storage: ArchiveStorage | None = None) -> None:
        self.storage = storage or get_archive_storage()

    def get(self, asset_id: str) -> StoredMedia:
        if len(asset_id) != 32 or any(c not in "0123456789abcdef" for c in asset_id):
            raise NotFound("素材不存在")
        path = self.storage.resolve(f"catalog/assets/{asset_id}.json")
        if not path.is_file():
            raise NotFound("素材不存在")
        return StoredMedia.model_validate(self.storage.read(path))

    def list(self) -> list[StoredMedia]:
        return [
            StoredMedia.model_validate(self.storage.read(p))
            for p in (self.storage.root() / "catalog/assets").glob("*.json")
        ]

    def save(self, media: StoredMedia) -> None:
        self.storage.write(
            self.storage.resolve(f"catalog/assets/{media.id}.json"),
            media.model_dump(mode="json", by_alias=True),
        )

    def register(
        self,
        source: Path,
        filename: str,
        category: MediaCategory,
        owner: str,
    ) -> StoredMedia:
        extension = Path(filename).suffix.lower()
        info = inspect(source, extension)
        kind: Literal["image", "video", "audio"]
        mime = EXTENSIONS[extension]
        kind = (
            "video"
            if mime.startswith("video/")
            else "audio"
            if mime.startswith("audio/")
            else "image"
        )
        if (
            category == "source"
            and kind != "video"
            or category in ("character", "product", "scene")
            and kind != "image"
            or category == "audio"
            and kind != "audio"
        ):
            raise BadRequest("原片需为 MP4，人物/产品/场景需为图片，音频分类需为音频")
        uid = uuid.uuid4().hex
        stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y/%m")
        key = f"library/{category}/{stamp}/{uid}{extension}"
        destination = self.storage.resolve(key)
        digest = self.storage.copy(source, destination)
        media = StoredMedia(
            id=uid,
            name=Path(filename).name,
            category=category,
            type=kind,
            mime_type=mime,
            size_bytes=destination.stat().st_size,
            path=key,
            sha256=digest,
            owner_id=owner,
            created_at=datetime.now(UTC),
            width=info["width"],
            height=info["height"],
            duration=info["duration"],
            has_audio=info["hasAudio"],
            url=f"/api/v1/assets/archive/{uid}/file",
        )
        self.ensure_preview(media)
        self.save(media)
        return media

    def ensure_preview(self, media: StoredMedia) -> None:
        key = f"library/previews/{media.id}.jpg"
        if create_preview(self.storage.resolve(media.path), self.storage.resolve(key), media.type):
            media.preview_path = key
            media.preview_url = f"/api/v1/assets/archive/{media.id}/thumbnail"
