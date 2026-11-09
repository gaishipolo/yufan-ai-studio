"""`AssetReferenceChecker` 的资产库 v2 实现。

v2 自己有一套 id（`library_assets.id`），大部分列**不是**对旧 `assets.id` 的引用。
唯一例外是生成溯源快照里的 `reference_asset_ids`：它记录「这次生成用了哪几张
旧素材库的图」——那些 id 属于旧 `assets`，删掉旧图会让历史详情页的参考图变空白。

所以这里只声明这一处，不把 v2 的 `library_asset_groups.asset_id` 之类算进来：
它们指向 v2 自己，由 v2 的 `library-references` 注入口负责，两套 id 空间不能混。

口径：**只要生成快照行还在，就算引用**。任务失败也保留——用户要看「当时用的
是哪几张图」，那正是排查失败原因的线索。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.ports.assets import AssetReferenceChecker

__all__ = ["GenerationAssetReferences"]

_MAX_IDS = 50


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class GenerationAssetReferences(AssetReferenceChecker):
    """生成溯源里记录的参考素材（旧 `assets.id`）。"""

    async def references(self, asset_ids: Sequence[str]) -> Mapping[str, Sequence[str]]:
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id][:_MAX_IDS]
        if not wanted:
            return {}

        # 局部 import：本类在 `modules.library.__init__` 注册，导入期越少越好
        from sqlalchemy import String, cast, select

        from db.session import get_session_factory
        from modules.library.models import LibraryAssetGeneration

        found: dict[str, list[str]] = {}
        async with get_session_factory()() as session:
            for asset_id in wanted:
                # JSON 列没法跨方言按元素查，先按子串粗筛（只会多捞），再精确比对
                rows = (
                    await session.execute(
                        select(
                            LibraryAssetGeneration.asset_id,
                            LibraryAssetGeneration.reference_asset_ids,
                        ).where(
                            cast(
                                LibraryAssetGeneration.reference_asset_ids, String
                            ).like(f"%{_escape_like(asset_id)}%", escape="\\")
                        )
                    )
                ).all()
                for row in rows:
                    if asset_id in (row.reference_asset_ids or []):
                        found.setdefault(asset_id, []).append(
                            f"生成资产 {row.asset_id} 的参考素材"
                        )
        return found
