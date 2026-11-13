"""按 id 空间**显式分流**地读素材元数据与 v2 可读性。

v1 `assets.id` 与 v2 `library_assets.id` 都是 32 位 uuid hex，形状不可区分。
调用方必须把空间一起交进来，由这里各问各的端口；绝不「先查 v2、查不到回退 v1」。

`ResolvedAssets` 刻意保留两份元数据映射，不把 `StoredMedia` 与 `LibraryAssetRef`
揉成一个带第二套权限语义的快照。v2 可读性也只转发 `LibraryLookup.readable_ids()`：
小组共享、封装成员等判据仍只有资产库 `access.py` 一份。没传 actor 时可读集合为空，
授权调用方漏传主体时会 fail-closed，不会退化成「查得到元数据就能用」。

名字不是授权：`resolve_names()` 只给调用方已经持有的 id 填展示名；能不能把内容
交给供应商，必须由授权闸另外检查 `readable_v2`。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.media import StoredMedia
from core.ports.library_lookup import LibraryAssetRef, get_library_lookup
from core.ports.media import get_asset_lookup
from modules.template.id_space import ASSET_ID_SPACE_V1, ASSET_ID_SPACE_V2

__all__ = ["ResolvedAssets", "resolve_assets", "resolve_names"]


@dataclass(frozen=True, slots=True)
class ResolvedAssets:
    """一批显式分流的元数据；`readable_v2` 只含 actor 当前可读的 v2 id。"""

    v1: Mapping[str, StoredMedia]
    v2: Mapping[str, LibraryAssetRef]
    readable_v2: frozenset[str] = frozenset()


def _partition(refs: Iterable[tuple[str | None, str]]) -> dict[str, set[str]]:
    wanted: dict[str, set[str]] = {ASSET_ID_SPACE_V1: set(), ASSET_ID_SPACE_V2: set()}
    for asset_id, space in refs:
        if asset_id and space in wanted:
            wanted[space].add(asset_id)
    return wanted


async def resolve_assets(
    session: AsyncSession,
    refs: Iterable[tuple[str | None, str]],
    *,
    actor: CurrentUser | None = None,
) -> ResolvedAssets:
    """按空间批量取元数据；给了 actor 就同时取 v2 可读集合。

    两个 `get_many()` 都只在对应空间非空时调用，因此「标成 v2 的 v1 id」不会
    触发任何旧库查询，更不存在查不到后的跨空间回退。
    """
    wanted = _partition(refs)
    legacy = (
        await get_asset_lookup().get_many(session, sorted(wanted[ASSET_ID_SPACE_V1]))
        if wanted[ASSET_ID_SPACE_V1]
        else {}
    )
    library = (
        await get_library_lookup().get_many(session, sorted(wanted[ASSET_ID_SPACE_V2]))
        if wanted[ASSET_ID_SPACE_V2]
        else {}
    )
    readable = (
        await get_library_lookup().readable_ids(
            session, actor, sorted(wanted[ASSET_ID_SPACE_V2])
        )
        if actor is not None and wanted[ASSET_ID_SPACE_V2]
        else set()
    )
    return ResolvedAssets(v1=legacy, v2=library, readable_v2=frozenset(readable))


async def resolve_names(
    session: AsyncSession, refs: Iterable[tuple[str | None, str]]
) -> dict[str, str]:
    """`[(id, 空间)]` → `{id: 展示名}`，两套 id 混在一批里也照办。

    不存在的 id 与未知空间名不出现在结果里。结果按 id 扁平化，前提与现有
    约定一致：同一个随机 hex id 不会同时属于两套空间。
    """
    assets = await resolve_assets(session, refs)
    names = {asset_id: row.name for asset_id, row in assets.v1.items()}
    names.update({asset_id: row.name for asset_id, row in assets.v2.items()})
    return names
