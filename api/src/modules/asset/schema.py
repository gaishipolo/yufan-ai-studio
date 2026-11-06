"""素材资产出入参。

`coverUrl` / `fileUrl` 是**服务端合成**的地址，不是数据库字段的直出：

- `coverUrl`：有托管封面时指向**本服务**的取封面端点（`/assets/{id}/cover`，
  同样带鉴权，换存储驱动不变）；没有托管封面时才退回记录的远端封面（供应商
  签名地址、演示数据），那种值是绝对 URL，可以直接交给 `<img>`。
- `fileUrl`：指向本服务的取文件端点，而不是对象存储地址。素材是私有内容，
  把一个可能带桶名/签名的直链交给前端，等于把访问控制交给 URL 的保密性。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from core.media import MediaCategory
from core.response import Page
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


class AssetSource(StrEnum):
    """记录是怎么进库的。不参与鉴权，只用于筛选与运营统计。"""

    UPLOAD = "upload"
    ARCHIVE = "archive"
    GENERATED = "generated"


class AssetOut(CamelModel):
    id: str
    name: str
    type: AssetType
    mime_type: str
    size_bytes: int
    width: int
    height: int
    duration: float
    has_audio: bool
    category: MediaCategory | None = None
    source: AssetSource
    cover_url: str
    file_url: str
    ref_count: int
    created_at: UtcDateTime


class AssetCounts(CamelModel):
    """分面计数：切类型 tab 上的数字。

    它按「除类型以外的全部筛选条件」统计，所以切 tab 不会看到三个互不相干的数字，
    也不会出现「选了图片但视频计数还是全站值」这种自相矛盾的显示。
    """

    all: int = 0
    image: int = 0
    video: int = 0
    audio: int = 0


class AssetListOut(Page[AssetOut]):
    counts: AssetCounts = Field(default_factory=AssetCounts)


class AssetListQuery(CamelModel):
    type: AssetTypeFilter = AssetTypeFilter.ALL
    keyword: str = ""
    category: MediaCategory | None = None
