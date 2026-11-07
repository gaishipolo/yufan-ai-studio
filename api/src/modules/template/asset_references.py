"""`AssetReferenceChecker` 的模板模块实现：封装（pack）引用了哪些归档素材。

`MediaPack.reference_ids` 里存的就是 `assets.id` —— 归档索引合并进 `assets` 表之后
`catalog/assets/<id>.json` 只是 DB 行的镜像，不再是另一套主键。以前注入口文档里
「模板引用的是归档素材 id、与素材库主键不同」是合并前的老事实，按它写就会漏掉
这一类引用：把已发布封装用到的原片移进回收站，包还在列表里，`prepare` 才报
「只能封装本人同类参考图片」，用户看不到真正的原因。

**只查 head 与草稿，不查历史版本**：`catalog/pack_versions/<uid>/<v>.json` 是冻结
证据，每次发布都留一份；把它算成引用会让几乎所有老素材都永远删不掉。历史版本
的可用性由 `app doctor` 的镜像/字节检查负责报告，不进删除拦截。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.ports.assets import AssetReferenceChecker
from infra.archive import ArchiveStorage, get_archive_storage

__all__ = ["PackAssetReferences"]

# head（已发布 / 停用）与草稿。历史版本见模块 docstring 的取舍说明。
_SOURCES = (
    ("catalog/packs", "封装"),
    ("catalog/pack_drafts", "封装草稿"),
)


class PackAssetReferences(AssetReferenceChecker):
    def __init__(self, storage: ArchiveStorage | None = None) -> None:
        # 延迟到查询时取：模块在 import 期注册，那时驱动可能还没按环境变量装配
        self._storage = storage

    @property
    def storage(self) -> ArchiveStorage:
        return self._storage if self._storage is not None else get_archive_storage()

    async def references(self, asset_ids: Sequence[str]) -> Mapping[str, Sequence[str]]:
        wanted = {asset_id for asset_id in asset_ids if asset_id}
        if not wanted:
            return {}
        found: dict[str, list[str]] = {}
        for prefix, label in _SOURCES:
            for key in self.storage.list_keys(prefix, suffix=".json"):
                value = self.storage.read(key)
                if not isinstance(value, dict):
                    continue
                # 兼容 snake_case 老数据；发布时写的是 model_dump(by_alias=True)
                refs = value.get("referenceIds") or value.get("reference_ids") or []
                if not isinstance(refs, list):
                    continue
                name = str(value.get("name") or "未命名")
                uid = str(value.get("id") or key.rsplit("/", 1)[-1].removesuffix(".json"))
                for asset_id in wanted:
                    if asset_id in refs:
                        found.setdefault(asset_id, []).append(f"{label}《{name}》({uid})")
        return found
