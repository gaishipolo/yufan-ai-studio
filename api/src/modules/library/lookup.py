"""`LibraryLookup` 注入口的 SQL 实现。

模板 / 任务 / 选择器在并存期要问「这个 v2 资产还在不在、是什么」，
但它们不能 import 本模块，所以由这里注册。

与旧 `AssetLookup` 并行存在，回答的是**两套 id 空间**的问题：拿旧 id 来查会
得到空结果，这是对的——把两套合并成「先查 A 再查 B」会让调用方无法判断
自己拿到的到底是哪一边的对象。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.ports.library_lookup import (
    LibraryAssetKind,
    LibraryAssetRef,
    LibraryAssetState,
    LibraryLookup,
    LibraryVisibility,
    register_library_lookup,
)
from modules.library.access import readable_rows
from modules.library.models import LibraryAsset

__all__ = ["SqlLibraryLookup", "lookup", "install"]


class SqlLibraryLookup(LibraryLookup):
    async def readable_ids(
        self, session: AsyncSession, actor: CurrentUser, asset_ids: Sequence[str]
    ) -> set[str]:
        # 判据在 `access.readable_rows`（含「封装成员」这一条读入口与小组判定），
        # 这里只转发：换个地方重写一份，第一处漂的永远是「小组共享」。
        rows = await readable_rows(session, list(asset_ids), actor)
        return set(rows)

    async def get_many(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, LibraryAssetRef]:
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
        if not wanted:
            return {}
        rows = (
            await session.scalars(select(LibraryAsset).where(LibraryAsset.id.in_(wanted)))
        ).all()
        return {
            row.id: LibraryAssetRef(
                id=row.id,
                owner_id=row.owner_id,
                kind=LibraryAssetKind(row.kind),
                name=row.name,
                state=LibraryAssetState(row.state),
                visibility=LibraryVisibility(row.visibility),
                mime_type=row.mime_type,
                storage_key=row.storage_key,
                size_bytes=row.size_bytes,
                duration_ms=int(row.duration * 1000) if row.duration else None,
                has_audio=row.has_audio,
            )
            for row in rows
        }


lookup = SqlLibraryLookup()


def install() -> None:
    """import 期挂到注入口。幂等——每次传的是同一个实例。"""
    register_library_lookup(lookup)
