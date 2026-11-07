"""「这个素材有没有被引用」的注入口。

素材删除（含归档素材移入回收站）要先问一句「还有谁在用它」。答案分散在
**别的模块**手里，而业务模块之间不许互相 import —— 所以走注入口。

这是一对多的聚合口，不是一个单例：当前有两个实现方

- `modules/task`：`video_tasks.reference_asset_ids`（生成时的参考图，DB 列）；
- `modules/template`：`MediaPack.reference_ids`（封装的 head / 草稿，归档 JSON）。

两者引用的都是 `assets.id` —— 归档索引合并进 `assets` 表之后，不再存在
「模板用的是另一套主键」这回事。**任何新增「把素材 id 写进自己表或文件」的模块，
都必须在这里登记一个来源**，否则删除会静默放过它。

一个来源查询失败时**不返回空映射**：那等于「查不到就算没人用」，会把正在被
引用的素材删掉。异常向上冒泡，由删除端点变成「引用检查暂时不可用」。

未接上任何来源时返回空映射，即「没人引用、可以删」。这是刻意的中性默认：
把未注册当成「可能有引用、一律拒绝」，会让删除功能在实现接上之前完全不可用，
而用户看得见的只有「删不掉」。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

__all__ = [
    "AssetReferenceChecker",
    "ask_asset_references",
    "has_asset_reference_checker",
    "reference_source_count",
    "register_asset_reference_checker",
    "reset_asset_reference_checker",
]


@runtime_checkable
class AssetReferenceChecker(Protocol):
    """谁在引用这些素材。"""

    async def references(self, asset_ids: Sequence[str]) -> Mapping[str, Sequence[str]]:
        """返回 `{asset_id: [引用方描述, ...]}`，没有引用的 id 可以不出现在结果里。

        描述是给人看的（例如「封装《圣诞活动》(pack1)」），会直接进 409 的报错
        文案，所以实现方要写清楚，别只丢一个内部 id。
        """
        ...


_checkers: list[AssetReferenceChecker] = []


def register_asset_reference_checker(port: AssetReferenceChecker) -> None:
    """登记一个来源。模块在 import 期调用；同一个对象重复登记直接忽略。

    允许多个：模块发现顺序不受控，硬规定「只能有一个」会逼着后来者去改别人
    的代码，最后往往是谁都不注册。
    """
    if any(existing is port for existing in _checkers):
        return
    _checkers.append(port)


def reset_asset_reference_checker() -> None:
    """仅测试用：清空全部来源。"""
    _checkers.clear()


def has_asset_reference_checker() -> bool:
    return bool(_checkers)


def reference_source_count() -> int:
    """自检用：部署形态对不对，看有几个来源接上了。"""
    return len(_checkers)


async def ask_asset_references(asset_ids: Sequence[str]) -> Mapping[str, Sequence[str]]:
    """聚合所有来源。任一来源抛异常则整体失败（宁可不让删，不可漏判）。"""
    if not _checkers or not asset_ids:
        return {}
    results = await asyncio.gather(
        *(checker.references(asset_ids) for checker in list(_checkers))
    )
    merged: dict[str, list[str]] = {}
    for result in results:
        for asset_id, descriptions in result.items():
            bucket = merged.setdefault(asset_id, [])
            for description in descriptions:
                if description not in bucket:
                    bucket.append(description)
    return {key: value for key, value in merged.items() if value}
