"""素材资产出入参。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from core.schema import CamelModel, UtcDateTime


class AssetType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class AssetTypeFilter(StrEnum):
    ALL = "all"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class AssetOut(CamelModel):
    id: str
    name: str
    type: AssetType
    mime_type: str
    size_bytes: int
    cover_url: str
    width: int
    height: int
    ref_count: int
    created_at: UtcDateTime


class AssetCounts(CamelModel):
    all: int = 0
    image: int = 0
    video: int = 0
    audio: int = 0


class AssetListOut(CamelModel):
    items: list[AssetOut] = Field(default_factory=list)
    total: int = 0
    counts: AssetCounts = Field(default_factory=AssetCounts)


class AssetListQuery(CamelModel):
    type: AssetTypeFilter = AssetTypeFilter.ALL
    keyword: str = ""


class UploadAssetIn(CamelModel):
    """预签名上传：前端拿到 uploadUrl 后直传对象存储，再回调确认。"""

    filename: str = Field(min_length=1, max_length=200)
    content_type: str = Field(default="application/octet-stream", max_length=64)
    size_bytes: int = Field(default=0, ge=0)


class UploadTicketOut(CamelModel):
    asset_id: str
    upload_url: str
    key: str
    expires_in: int = 900
