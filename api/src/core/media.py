"""跨模块共用的媒体描述词汇；权限判断属于各自业务 service。"""

from __future__ import annotations

from typing import Literal

from core.schema import CamelModel, UtcDateTime

MediaCategory = Literal["source", "character", "product", "scene", "audio"]


class MediaFile(CamelModel):
    id: str
    name: str
    category: MediaCategory
    type: Literal["image", "video", "audio"]
    mime_type: str
    size_bytes: int
    path: str
    sha256: str
    created_at: UtcDateTime
    width: int = 0
    height: int = 0
    duration: float = 0
    has_audio: bool = False
    url: str
    preview_url: str | None = None


class StoredMedia(MediaFile):
    owner_id: str
    preview_path: str | None = None
