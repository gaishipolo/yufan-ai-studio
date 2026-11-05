"""素材归档的 HTTP 契约。"""

from __future__ import annotations

from pydantic import Field

from core.media import MediaCategory, MediaFile
from core.schema import CamelModel


class ArchivedAsset(MediaFile):
    pass


class ArchiveList(CamelModel):
    root: str
    items: list[ArchivedAsset]
    total: int
    used_bytes: int


class ImportAsset(CamelModel):
    relative_path: str = Field(min_length=1, max_length=500)
    category: MediaCategory
