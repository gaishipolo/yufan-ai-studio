"""跨模块共用的媒体描述词汇；权限判断属于各自业务 service。

这里只留**素材本身**的词汇。封装（参考图组合、逐图用途、发布版本）曾经也在
本文件（`MediaPack` / `PackBinding`），但它只有模板库一个消费者，已于
2026-11-08 随模板库重建收回 `modules/template`：放在 core 的判据是「两个以上
互不认识的模块都要用」，不是「听起来像基础设施」。
"""

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
    shared: bool = False
    shared_at: UtcDateTime | None = None
    deleted_at: UtcDateTime | None = None
    state_version: int = 0
