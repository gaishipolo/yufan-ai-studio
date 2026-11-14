"""`library-references` 注入口的模板封装实现：v2 资产被哪些已发布模板还在用。

## 为什么这条声明不能省

模板的槽位可以「封装时固定」（`binding=fixed`）一张图。发布那一刻这张图就是
模板的一部分：删掉它，同一版本再编译时 `{{Mixed N}}` 会指向一张取不到的素材，
而模板本身看起来毫无变化。用户彻底删除是不可逆的（v2 `purge` 不进回收站），
必须在删之前就把这条引用数出来。

## 为什么只查已发布槽位

`template_spec_slots` 只在发布时写入（见 `models.TemplateSpecSlot` 的说明）。
草稿存在 `template_specs.draft` 的 JSON 里，不进这张表——草稿指向的素材被删，
作者改一下槽位就行，不构成不可逆损失。反过来，把草稿也算进来会让「编辑到一半
试了两张图」永久占住素材。

## 为什么不按 category / visibility 过滤

故意**只按 asset_id 查**，不附加任何谓词。数的是「谁的固定槽位里写着这个 id」，
与这条素材此刻是不是在回收站无关：引用还在，用户把它从回收站恢复之后模板就该
还能用；按可见性过滤会**少一条引用**，而少一条引用 = 放行一次不可逆删除。

这与 `modules/library/references.py` 里「指向不存在封装的孤儿行也要算引用」
是同一条取舍：查不清楚的一律当「有人在用」。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.template_pack.models import (
    TemplateSpec,
    TemplateSpecSlot,
    TemplateSpecVersion,
)

__all__ = ["SpecFixedAssets"]


class SpecFixedAssets:
    """`template_spec_slots.asset_id`：已发布版本里固定槽位咬住的 v2 资产。

    用调用方传进来的 `session`（v2 注入口的约定，见
    `core/ports/library_references.py` 的「为什么不并发聚合」），不像 v1 那样
    自己开 session——删除与引用检查在同一个事务里，才不会出现「查到没人用、
    提交前有人刚引用上」的窗口。
    """

    async def references(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]:
        if not asset_ids:
            return {}
        rows = await session.execute(
            select(
                TemplateSpecSlot.asset_id,
                TemplateSpec.name,
                TemplateSpec.spec_key,
            )
            .join(
                TemplateSpecVersion,
                TemplateSpecVersion.id == TemplateSpecSlot.version_id,
            )
            .join(TemplateSpec, TemplateSpec.id == TemplateSpecVersion.spec_id)
            .where(
                TemplateSpecSlot.asset_id.in_(set(asset_ids)),
                TemplateSpecSlot.binding == "fixed",
            )
        )
        found: dict[str, list[str]] = {}
        for asset_id, name, key in rows:
            if not asset_id:  # pragma: no cover - where 已经排除 null
                continue
            found.setdefault(asset_id, []).append(f"{name}（{key}）")
        return found
