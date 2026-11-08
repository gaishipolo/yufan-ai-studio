"""素材模块对 `AssetLookup` 注入口的实现。

模板库在 prepare 阶段要读素材元数据（归属、分类、是否已删、时长、有没有音轨），
但它不能 import 素材模块——所以由素材模块把自己注册进去。

**复用 `ArchiveAssetRepository.to_media()`**：DB 行转 `StoredMedia` 的映射只该有
一份。以前 `catalog/assets/*.json` 镜像里也有一份同样的映射，两份漂移过一次
（镜像里少了 `shared`，导致「已分享」的原片在模板侧看不见）。镜像现在只是
派生投影，但映射仍只有这一个入口。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.media import StoredMedia
from core.ports.media import AssetLookup, bind_asset_lookup
from modules.asset.archive_repository import ArchiveAssetRepository
from modules.asset.models import Asset

__all__ = ["SqlAssetLookup", "lookup", "install"]


class SqlAssetLookup(AssetLookup):
    async def get_many(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> dict[str, StoredMedia]:
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
        if not wanted:
            return {}
        rows = (
            await session.scalars(select(Asset).where(Asset.id.in_(wanted)))
        ).all()
        return {row.id: ArchiveAssetRepository.to_media(row) for row in rows}


lookup = SqlAssetLookup()


def install() -> None:
    """import 期挂到注入口。幂等——重复调用只是覆盖同一个实现。"""
    bind_asset_lookup(lookup)
