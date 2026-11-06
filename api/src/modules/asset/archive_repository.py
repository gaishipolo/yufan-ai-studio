"""归档素材仓储：`assets` 表是索引，`catalog/assets/*.json` 是只读镜像。

## 为什么不再 glob JSON

以前列表是「扫 `catalog/assets/*.json` 再按 owner 过滤」：共享盘上文件一多，
每次翻页都要把整个目录读一遍；而且 JSON 与数据库各自记一份 `owner_id`，
权限判断就变成了「信哪个文件」的问题。现在查询走 `assets` 表（`source='archive'`），
分页与过滤交给数据库。

## JSON 为什么还写

替换模板（`modules/template/replacement_*`）在 worker 里同步读
`catalog/assets/{id}.json`，是「零改动」的前提。所以新入库的归档素材
**同时写一份 JSON**，内容由 DB 行的同一套映射派生，不额外维护第二份真相。

写失败即整次入库失败（回滚行、删掉刚复制的对象）：留下「有行没 JSON」的记录，
替换模板会在 `prepare` 阶段报「素材不存在」，而用户看到的却是列表里明明有。

## 目录归属

`storage_key` 是**归档根**下的相对 key（如 `library/source/2026/11/xxx.mp4`），
与上传素材（相对对象存储根）不是同一个命名空间。区别靠 `source` 列表达：
`archive` 走 `ArchiveStorage`，其余走 `ObjectStorage`。不要拿这个 key 去问
`get_storage()`，那是另一棵树。
"""

from __future__ import annotations

from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions import NotFound
from core.media import MediaCategory, StoredMedia
from db.repository import BaseRepository
from modules.asset.models import Asset
from modules.asset.schema import AssetSource

CATEGORIES = ("source", "character", "product", "scene", "audio")

# 归档素材只允许这五种分类；库里为 NULL 的行不是归档来源，读出来也不该假装是
_DEFAULT_CATEGORY: MediaCategory = "source"

AssetKind = Literal["image", "video", "audio"]


class ArchiveAssetRepository:
    """按 `source='archive'` 限定作用域的素材仓储（异步）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = BaseRepository(session, Asset)

    async def get(self, asset_id: str) -> Asset:
        """按 id 取归档行；格式不对、不存在、不是归档来源，一律「素材不存在」。"""
        if len(asset_id) != 32 or any(c not in "0123456789abcdef" for c in asset_id):
            raise NotFound("素材不存在")
        row = await self.repo.get(asset_id)
        if row is None or row.source != AssetSource.ARCHIVE:
            raise NotFound("素材不存在")
        return row

    async def list(self, owner: str, category: str = "all") -> list[Asset]:
        stmt = (
            select(Asset)
            .where(
                Asset.owner_id == owner,
                Asset.source == AssetSource.ARCHIVE,
            )
            .order_by(Asset.created_at.desc(), Asset.id.desc())
        )
        if category != "all":
            stmt = stmt.where(Asset.category == category)
        return list((await self.session.scalars(stmt)).all())

    async def add(self, row: Asset) -> Asset:
        self.session.add(row)
        await self.session.flush()
        return row

    @staticmethod
    def to_media(row: Asset) -> StoredMedia:
        """DB 行 → 归档出参。

        路径与 URL 都按「归档根」拼，`path` 是前端下载时拼 Z 盘路径用的相对 key。
        """
        preview_key = row.cover_key or None
        base = f"{settings.api_prefix}/assets/archive/{row.id}"
        return StoredMedia(
            id=row.id,
            name=row.name,
            category=cast(MediaCategory, row.category or _DEFAULT_CATEGORY),
            type=cast(AssetKind, row.type),
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            path=row.storage_key,
            sha256=row.sha256 or "",
            owner_id=row.owner_id,
            created_at=row.created_at,
            width=row.width,
            height=row.height,
            duration=row.duration,
            has_audio=row.has_audio,
            url=f"{base}/file",
            preview_url=f"{base}/thumbnail" if preview_key else None,
            preview_path=preview_key,
        )
