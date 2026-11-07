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
    """列表出参。

    **没有 `root`**：那是本地归档根的绝对路径，远端驱动下根本不存在，
    前端也早已不再展示（原来「归档位置」那一行在切 S3 后恒为空）。
    要位置信息就用 `storageKey`，它才是与驱动无关的那一层。
    """

    items: list[ArchivedAsset]
    total: int
    used_bytes: int
    # 配额按「本人未删除的归档素材」算，与上面的 `used_bytes`（当前筛选结果的
    # 占用）不是一个口径：在 public / trash 分池里拿 usedBytes 当已用容量会算错。
    quota_bytes: int = 0
    owner_used_bytes: int = 0
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
