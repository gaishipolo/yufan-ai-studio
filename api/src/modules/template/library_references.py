"""`library-references` 注入口的模板库实现：v2 资产被哪些封装还在用。

与同目录 `asset_references.py`（v1 `assets.id`）**是两套 id 空间的两次声明**，
不是同一个东西的两个名字。并存期 `template_pack_references.asset_id` 一列混装
两套 id（见 `modules/template/id_space.py`），删除侧却是两个互不相通的注入口：
旧素材删除问 v1，v2 的「彻底删除」问这里。

## 为什么这条声明不能省

`tests/test_architecture.py::test_every_library_reference_writer_declares_a_source`
用 AST 扫到 `modules/template/schema.py` 里的 `reference_library_ids`（草稿契约）
就会要求模块声明来源。省掉声明的后果不是 CI 红，而是更早的一次静默放行：
用户彻底删除一张图 → `ask_library_references()` 里没有模板这一路 → 返回空
→ 删除成功 → 已发布的封装卡片全变成裂图，且**不可逆**（v2 `purge` 不进回收站）。

## 为什么不按 `asset_id_space` 过滤

故意**只按 asset_id 查**，不加 `asset_id_space == 'v2'` 的谓词。两个理由：

1. 两套 id 都是 uuid4（`db/base.py::UUIDMixin`），跨表撞 id 的概率为零，
   多查不会误伤另一侧；
2. 空间标记万一写错（历史行、手工修库），按空间过滤会**少一条引用**，
   而少一条引用 = 放行一次不可逆删除。宁可多保护。

这与 `modules/library/references.py` 里「指向不存在封装的孤儿行也要算引用」
是同一条取舍：查不清楚的一律当「有人在用」。

## 不查的四处，以及为什么

- `template_pack_versions.reference_ids`：与 v1 同口径——冻结版本是历史证据，
  发布过就留一份，把它算成引用会让老素材永远删不掉。素材目录的卡片与包内预览
  用的都是**当前**版本，历史版本要显式带 `?version=` 才读得到。
- `replacement_run_inputs.asset_id`：字节在 prepare 时已物化进归档
  （`storage_key`），回放不读任何一侧的表——这正是 v1 下线后历史任务仍可复核的依据。
- `video_templates.media_order[*].assetId`：它是「哪张图占哪个槽位」的清单，
  出图时按 id 与**本次运行**的参考图配对（`_media_in_mixed_order`），不按它取字节；
  且它必然指向一个已发布封装，那个封装的 head 已经拦住了。
- `template_packs.cover_asset_id`：封面取自封装参考图（`_shape()` 里不在
  `reference_ids` 就回退第一张），随上面那条一起被覆盖。

四处都不影响「删完之后还能不能再出一条同样的片」，所以不拦。反过来说，
新增一处**会**让历史无法复现的写入，就必须在这里补一条声明。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from modules.template.repository import packs_referencing

__all__ = ["PackLibraryReferences"]


class PackLibraryReferences:
    """`template_pack_references.asset_id`：封装 head 里引用的 v2 资产。

    用调用方传进来的 `session`（v2 注入口的约定，见
    `core/ports/library_references.py` 的「为什么不并发聚合」），不像 v1 那样
    自己开 session——删除与引用检查在同一个事务里，才不会出现「查到没人用、
    提交前有人刚引用上」的窗口。
    """

    async def references(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]:
        return await packs_referencing(session, asset_ids)
