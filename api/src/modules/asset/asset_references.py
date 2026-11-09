"""素材被**生成配方**引用时的来源声明（M3）。

## 为什么素材模块自己也要声明一个来源

`asset_refs` 落在本模块，引用关系却指向 `assets.id`。删除保护的判据是
「谁在用它」，而"用它生成过一张成品"是一种真实使用：删掉源图，成品的血缘在
中途断掉，复现与合规追溯一起失效。**它不能因为"表在同一个模块里"就被漏掉**
——`tests/test_architecture.py` 的 AST 守卫会扫到 `reference_asset_ids` 这个
字段名，模块没声明来源就直接红。

## 只算「产出还活着」的配方

记录里的配方不都是有效使用：一次 8 连拍的候选可能全被弃用、也可能过期。
把这些算成引用会让源素材永远删不掉——而用户看到的是"这张图明明没人用了"。
所以只有**已采纳且未删除的产物**才算引用。

代价是：产物被删之后，这条血缘就不再阻止删除源图。这是对的——血缘的展示
仍然在（`/lineage` 读的是 `asset_refs`），删除保护回答的是另一个问题：
"现在还有没有人依赖它"。

## 自己开 session

与模板侧的实现同形（`modules/template/asset_references.py`）：调用方（素材删除）
在自己的事务里，不该把本模块的读也塞进去。两边的数据一致性需求不同，
共享事务只会让一方的问题锁住另一方。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select

from core.ports.assets import AssetReferenceChecker
from db.session import get_session_factory
from modules.asset.lifecycle import LIVE_STAGE
from modules.asset.models import Asset
from modules.asset.recipe_models import AssetRef

__all__ = ["RecipeAssetReferences"]


class RecipeAssetReferences(AssetReferenceChecker):
    async def references(self, asset_ids: Sequence[str]) -> Mapping[str, Sequence[str]]:
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
        if not wanted:
            return {}
        async with get_session_factory()() as session:
            rows = (
                await session.execute(
                    select(AssetRef.asset_id, Asset.name)
                    .join(Asset, Asset.recipe_id == AssetRef.recipe_id)
                    .where(
                        AssetRef.asset_id.in_(wanted),
                        Asset.stage == LIVE_STAGE,
                        Asset.deleted_at.is_(None),
                        # 自引用是数据错误，但展示成"自己用自己"只会让人以为界面坏了
                        Asset.id != AssetRef.asset_id,
                    )
                )
            ).all()
        merged: dict[str, list[str]] = {}
        for asset_id, name in rows:
            merged.setdefault(asset_id, []).append(f"生成产物「{name}」")
        return merged
