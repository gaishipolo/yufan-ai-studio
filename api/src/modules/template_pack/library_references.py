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

## 回收站里的模板照样算引用

软删（`deleted_at`）只是把模板从列表里藏起来，`_versions` / `_slots` 一行没动，
恢复之后引用立刻回来。所以这里**不**按 `deleted_at` 过滤：按它过滤会给用户一个
「删了模板就能删素材」的错觉，而恢复之后那张图已经不可逆地没了。

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
    TemplateCase,
    TemplateSpec,
    TemplateSpecSlot,
    TemplateSpecVersion,
)

__all__ = ["CaseSnapshots", "SpecDemoCases", "SpecFixedAssets"]


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


class SpecDemoCases:
    """`template_specs.draft` 里的 `demoAssetId`：模板库卡片上的展示案例。

    ## 为什么它也算引用

    案例不是「一份可以随时换的装饰」：卡片上那张封面、悬停自动播的那段视频，
    都是这条素材本身（`/library/assets/{id}/file`）。素材被彻底删除之后，卡片
    不会报错，只会安静地退化成渐变块 —— 作者看不出是模板坏了还是素材没了。

    ## 为什么扫全表在 Python 里判

    `draft` 是 JSON 列（PG 是 jsonb、sqlite 是 text）。两种方言的路径查询写法
    不同（`->>` vs `json_extract`），在这条「不可逆删除」的路径上引入方言分支
    不值得：模板规格是「一个人写、整份替换」的小表，全量扫一遍的代价远低于
    一次方言写错导致的漏检。宁可多拦一次，也不要少算一条引用。

    只认未删除的模板：软删的模板（`deleted_at` 非空）恢复之后案例立刻回来，
    所以**它们照样算引用**。这一点与 `SpecFixedAssets` 同一个口径 ——
    按 `deleted_at` 过滤会给人「删了模板就能删素材」的错觉。
    """

    async def references(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]:
        wanted = {asset_id for asset_id in asset_ids if asset_id}
        if not wanted:
            return {}
        rows = await session.execute(
            select(TemplateSpec.name, TemplateSpec.spec_key, TemplateSpec.draft)
        )
        found: dict[str, list[str]] = {}
        for name, key, draft in rows:
            if not isinstance(draft, dict):
                continue
            # 草稿是 camelCase JSON（与契约同形），兼容一下早年写入的 snake_case。
            demo_id = draft.get("demoAssetId") or draft.get("demo_asset_id")
            if isinstance(demo_id, str) and demo_id in wanted:
                found.setdefault(demo_id, []).append(f"{name}（{key}）的展示案例")
        return found


class CaseSnapshots:
    """`template_cases.config`：模板案例快照里咬住的 v2 资产。

    ## 为什么案例快照也要算引用

    案例存的是模板配置的一份**快照**（含固定槽位绑定的 `assetId` 与展示案例
    `demoAssetId`），复用者拿它复制出新模板时，固定槽位就指着那张图。素材被彻底
    删除之后，复用出来的新模板会安静地裂图 —— 案例本身看着毫无异常。

    ## 口径：全表扫，**不**按状态过滤

    下架（`status=offline`）与软删（`deleted_at` 非空）的案例都照样算引用：
    它们随时能重新上架 / 恢复，引用会立刻回来。按状态过滤等于告诉用户
    「下架了就能删素材」，等他恢复案例时那张图已经不可逆地没了。

    与 `SpecFixedAssets` 同一个取舍：**查不清楚的一律当「有人在用」**。
    """

    async def references(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]:
        wanted = {asset_id for asset_id in asset_ids if asset_id}
        if not wanted:
            return {}
        rows = await session.execute(select(TemplateCase.name, TemplateCase.config))
        found: dict[str, list[str]] = {}
        for name, config in rows:
            if not isinstance(config, dict):
                continue
            used: set[str] = set()
            demo_id = config.get("demoAssetId") or config.get("demo_asset_id")
            if isinstance(demo_id, str):
                used.add(demo_id)
            for slot in config.get("slots") or []:
                if not isinstance(slot, dict):
                    continue
                if (slot.get("binding") or "") != "fixed":
                    continue
                asset_id = slot.get("assetId") or slot.get("asset_id")
                if isinstance(asset_id, str):
                    used.add(asset_id)
            for asset_id in used & wanted:
                found.setdefault(asset_id, []).append(f"模板案例《{name}》")
        return found
