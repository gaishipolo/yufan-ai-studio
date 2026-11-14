"""批量取 v2 资产元数据与可读性。

2026-11-13 之前这里按 id 空间**显式分流**（v1 `assets.id` / v2 `library_assets.id`）。
旧素材库下线后只剩一套 id 空间，分流消失，但仍然保留一个收敛点：

- 可读性只转发 `LibraryLookup.readable_ids()`：小组共享、封装成员等判据仍只有
  资产库 `access.py` 一份，模板侧不重写规则；
- 没传 actor 时可读集合为空，授权调用方漏传主体时会 fail-closed，不会退化成
  「查得到元数据就能用」；
- 名字不是授权：`resolve_names()` 只给调用方已经持有的 id 填展示名；能不能把内容
  交给供应商，必须由授权闸另外检查 `readable_v2`。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.ports.library_lookup import LibraryAssetRef, get_library_lookup

__all__ = ["ResolvedAssets", "resolve_assets", "resolve_names"]


@dataclass(frozen=True, slots=True)
class ResolvedAssets:
    """一批资产元数据；`readable_v2` 只含 actor 当前可读的 id。"""

    v2: Mapping[str, LibraryAssetRef]
    readable_v2: frozenset[str] = frozenset()


async def resolve_assets(
    session: AsyncSession,
    refs: Iterable[str],
    *,
    actor: CurrentUser | None = None,
) -> ResolvedAssets:
    """批量取元数据；给了 actor 就同时取可读集合。

    只在 id 列表非空时查询，因此空输入不会产生任何往返。
    """
    wanted = sorted({asset_id for asset_id in refs if asset_id})
    library = (
        await get_library_lookup().get_many(session, wanted) if wanted else {}
    )
    readable = (
        await get_library_lookup().readable_ids(session, actor, wanted)
        if actor is not None and wanted
        else set()
    )
    return ResolvedAssets(v2=library, readable_v2=frozenset(readable))


async def resolve_names(session: AsyncSession, refs: Iterable[str]) -> dict[str, str]:
    """`[id]` → `{id: 展示名}`；不存在的 id 不出现在结果里。"""
    assets = await resolve_assets(session, refs)
    return {asset_id: row.name for asset_id, row in assets.v2.items()}
