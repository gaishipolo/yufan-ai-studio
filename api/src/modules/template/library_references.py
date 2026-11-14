"""`library-references` 的替换链实现：run 输入里的 v2 资产**显式不拦**。

## 为什么这里是一个「永远返回空」的 checker

`PrepareReplacement.reference_library_ids`（`referenceLibraryIds`）是 v2 资产 id
的集合，它们会被冻进 `replacement_run_inputs.asset_id`。也就是说本模块**确实**
把 `library_assets.id` 写进了自己的表，静态守卫
（`test_architecture.py::test_every_library_reference_writer_declares_a_source`）
因此要求本模块声明一个引用来源。

但把它算成「有人在用」是错的，而且代价很大：prepare 时字节已经物化进归档
（`storage_key`），回放与重跑都不再读资产库。若按 run 输入拦删除，几乎每一张
用过的图都会永远删不掉——这是 2026-11-08 重建时明确接受的取舍（见
`modules/template_pack/library_references.py` 的「不查的四处」）。

所以这里**如实声明 + 显式返回空**，而不是靠「漏声明」让守卫闭嘴：
漏声明的失败方向是静默放行一次不可逆的删除，而「我们评估过、故意不拦」必须
写在代码里，下一个人改主意时才知道要改哪。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["RunInputsNotBlocking"]


class RunInputsNotBlocking:
    """run 输入的 v2 id 不算删除引用（字节已物化，回放不读资产库）。

    看完上面那段再决定要不要让它开始返回东西：一旦返回，几乎所有用过的素材
    都会变成「不能彻底删除」，而收益只是「历史档案的缩略图链接不会 404」。
    """

    async def references(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]:
        return {}
