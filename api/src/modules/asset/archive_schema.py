"""素材归档的 HTTP 契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from core.media import MediaCategory, MediaFile
from core.schema import CamelModel, UtcDateTime

# 列表筛选：声明成 Literal 而不是裸 str，非法值由框架回 422，
# 也让契约里带上取值域（前端 `ArchiveSearch` 仍手写窄类型，二者需保持一致）。
ArchiveCategory = Literal["all", "source", "character", "product", "scene", "audio"]
ArchiveScope = Literal["mine", "public", "trash"]
ArchiveState = Literal["all", "private", "shared"]


class ArchivedAsset(MediaFile):
    owner_id: str = ""
    shared: bool = False
    shared_at: UtcDateTime | None = None
    deleted_at: UtcDateTime | None = None
    state_version: int = 0
    editable: bool = False
    download_allowed: bool = True


class ArchiveList(CamelModel):
    root: str
    items: list[ArchivedAsset]
    total: int
    used_bytes: int
    page: int = 1
    page_size: int = 200


class ArchiveChange(CamelModel):
    expected_version: int = Field(ge=0)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    shared: bool | None = None
    deleted: bool | None = None


class ImportAsset(CamelModel):
    relative_path: str = Field(min_length=1, max_length=500)
    category: MediaCategory
