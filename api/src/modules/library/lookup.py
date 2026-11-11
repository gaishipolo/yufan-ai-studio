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
    LibraryTextRef,
    LibraryVisibility,
    register_library_lookup,
)
from modules.library.access import readable_rows
from modules.library.models import LibraryAsset, LibraryTextVersion

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

    async def read_text_many(
        self, session: AsyncSession, actor: CurrentUser, asset_ids: Sequence[str]
    ) -> Mapping[str, LibraryTextRef]:
        """文本正文快照。**判据只有 `readable_rows` 一份**：共享出去的、
        靠封装成员身份读到的都算，失权的一律不返回，调用方按缺失报错而不是
        「查不到就当没引用」把别人的私有文案跳过。

        正文来自 `library_text_versions` 的最新版，不读 blob：注入发生在提交期，
        那时候不该再为每条引用开一次对象存储的读。版本行是行内不可变快照，
        编辑资产不会改写它，重试拿到的是同一段正文。
        """
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
        if not wanted:
            return {}
        rows = await readable_rows(session, wanted, actor)
        text_ids = [
            asset_id
            for asset_id, row in rows.items()
            if row.kind == "text" and row.state == "active"
        ]
        if not text_ids:
            return {}
        versions = (
            await session.scalars(
                select(LibraryTextVersion).where(
                    LibraryTextVersion.asset_id.in_(text_ids)
                )
            )
        ).all()
        latest: dict[str, LibraryTextVersion] = {}
        for version in versions:
            current = latest.get(version.asset_id)
            if current is None or version.version > current.version:
                latest[version.asset_id] = version
        return {
            asset_id: LibraryTextRef(
                id=asset_id,
                name=rows[asset_id].name,
                version=version.version,
                sha256=version.sha256,
                content=version.content,
                size_bytes=version.size_bytes,
            )
            for asset_id, version in latest.items()
        }


lookup = SqlLibraryLookup()


def install() -> None:
    """import 期挂到注入口。幂等——每次传的是同一个实例。"""
    register_library_lookup(lookup)
