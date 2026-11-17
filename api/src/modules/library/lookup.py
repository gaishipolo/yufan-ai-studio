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
from modules.library.models import (
    LibraryAsset,
    LibraryTextVersion,
)
from modules.library.tags import all_tags, tag_filter_clause

__all__ = ["SqlLibraryLookup", "lookup", "install"]

#: 候选窗口 = `limit` × 这个倍数。见 `find_ids()` 的 docstring：可读性只能逐行判，
#: 窗口太窄会让「过滤完的条数」取决于别人的库有多大。
_CANDIDATE_FACTOR = 5
#: 单页最小条数。`limit=1` 时乘出来的窗口太小，一次只看 5 条就翻页很浪费。
_MIN_PAGE = 50
#: 翻页上限。再大就退化成「把整张表拉进内存」，而调用方要的只是几十个 id。
_MAX_CANDIDATES = 500


class SqlLibraryLookup(LibraryLookup):
    async def readable_ids(
        self, session: AsyncSession, actor: CurrentUser, asset_ids: Sequence[str]
    ) -> set[str]:
        # 判据在 `access.readable_rows`（含小组判定），这里只转发：
        # 换个地方重写一份，第一处漂的永远是「小组共享」。
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
                cover_key=row.cover_key,
                size_bytes=row.size_bytes,
                sha256=row.sha256 or "",
                width=row.width,
                height=row.height,
                duration_ms=int(row.duration * 1000) if row.duration else None,
                has_audio=row.has_audio,
            )
            for row in rows
        }

    async def read_text_many(
        self, session: AsyncSession, actor: CurrentUser, asset_ids: Sequence[str]
    ) -> Mapping[str, LibraryTextRef]:
        """文本正文快照。**判据只有 `readable_rows` 一份**：共享出去的都算，
        失权的一律不返回，调用方按缺失报错而不是
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

    async def find_ids(
        self,
        session: AsyncSession,
        actor: CurrentUser,
        *,
        kind: LibraryAssetKind | None = None,
        tag_ids: Sequence[str] = (),
        owner_id: str | None = None,
        limit: int = 50,
    ) -> list[str]:
        """按条件列 id。`state=active` 是硬过滤，`tag_ids` 按维度筛选。

        判据与 `GET /library/selection` 同口径（同一张表、同一套标签展开、
        同一条 `readable_rows`），差别只是这里只回 id：调用方（演示数据、导入器）
        拿着 id 还要自己决定挑哪几张，先取 24 份元数据纯属浪费。

        **分页捞到够为止**：可读性是逐行判的（`readable_rows` 要查小组），压不进
        这一条 SQL，所以只能「取一批 → 过滤 → 不够再取」。只取一次的话，
        「凑到几条」取决于库里别人有多少资产排在前面——调用方会静默拿到一个偏短的
        列表（演示数据少一半，而且不报错）。翻页上限 `_MAX_CANDIDATES` 兜住
        「整库都不可读」这种极端情况，避免扫全表。
        """
        picked: list[str] = []
        offset = 0
        page_size = max(limit * _CANDIDATE_FACTOR, _MIN_PAGE)
        # 标签条件只算一次：它在循环里每轮都要查一次标签表，而结果与 offset 无关。
        tag_clause = (
            tag_filter_clause(await all_tags(session), tag_ids) if tag_ids else None
        )
        while len(picked) < limit and offset < _MAX_CANDIDATES:
            query = select(LibraryAsset.id).where(LibraryAsset.state == "active")
            if kind is not None:
                query = query.where(LibraryAsset.kind == kind.value)
            if owner_id:
                query = query.where(LibraryAsset.owner_id == owner_id)
            if tag_clause is not None:
                query = query.where(tag_clause)
            page = list(
                (
                    await session.scalars(
                        query.order_by(LibraryAsset.created_at, LibraryAsset.id)
                        .offset(offset)
                        .limit(page_size)
                    )
                ).all()
            )
            if not page:
                break
            readable = await readable_rows(session, page, actor)
            picked.extend(asset_id for asset_id in page if asset_id in readable)
            offset += len(page)
        return picked[:limit]


lookup = SqlLibraryLookup()


def install() -> None:
    """import 期挂到注入口。幂等——每次传的是同一个实例。"""
    register_library_lookup(lookup)
