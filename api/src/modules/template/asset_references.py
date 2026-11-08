"""`AssetReferenceChecker` 的模板库实现：哪些封装的 head 引用了这些素材。

数据进数据库之后，这件事从「扫 `catalog/packs/*.json` 逐个比对 `referenceIds`」
变成一条带索引的 join（`template_pack_references.asset_id`）。

**只查 head，不查历史版本**：`template_pack_versions` 是冻结证据，每次发布都留
一份；把它算成引用会让几乎所有老素材永远删不掉。历史版本的可用性由
`app doctor` 的检查负责报告，不进删除拦截。

与旧实现的另一个差别：**不再需要自己实现"哪张图属于哪个包"的文字拼装**——
`packs_referencing()` 返回的已经是给人看的描述（「封装《圣诞活动》(pack1)」），
会直接进 409 的报错文案。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.ports.assets import AssetReferenceChecker
from db.session import get_session_factory
from modules.template.repository import packs_referencing

__all__ = ["PackAssetReferences"]


class PackAssetReferences(AssetReferenceChecker):
    async def references(self, asset_ids: Sequence[str]) -> Mapping[str, Sequence[str]]:
        """自己开 session：注入口的调用方（素材删除）已经是异步上下文，
        但它在自己事务里，不该把模板库的读也塞进那个事务——两个模块的数据
        一致性需求不同，共享事务只会让一方的问题锁住另一方。"""
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
        if not wanted:
            return {}
        async with get_session_factory()() as session:
            return await packs_referencing(session, wanted)
