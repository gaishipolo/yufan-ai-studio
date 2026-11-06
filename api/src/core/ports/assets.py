"""「这个素材有没有被引用」的注入口。

素材删除要先问一句「还有谁在用它」。答案在**别的模块**手里（任务 / 分镜 / 镜头），
而业务模块之间不许互相 import —— 所以走注入口。

当前实现方：`modules/task`（`video_tasks.reference_asset_ids`，生成时的参考图）。
`modules/template` 不走这里：它引用的是**归档素材 id**（`catalog/assets/*.json`
里的 media id），与素材库的 `assets` 表不是同一套主键，删除归档走回收站标记。

未接上时返回空映射，即「没人引用、可以删」。这是刻意的中性默认：把未注册当成
「可能有引用、一律拒绝」，会让删除功能在实现接上之前完全不可用，而用户看得见的
只有「删不掉」。反过来真的漏掉引用方，代价是删完那几个页面出现空白图——所以
新增「把素材 id 写进自己表」的模块时，必须同时在这里登记一个实现。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

__all__ = [
    "AssetReferenceChecker",
    "ask_asset_references",
    "has_asset_reference_checker",
    "register_asset_reference_checker",
    "reset_asset_reference_checker",
]


@runtime_checkable
class AssetReferenceChecker(Protocol):
    """谁在引用这些素材。"""

    async def references(self, asset_ids: Sequence[str]) -> Mapping[str, Sequence[str]]:
        """返回 `{asset_id: [引用方描述, ...]}`，没有引用的 id 可以不出现在结果里。

        描述是给人看的（例如「模板《圣诞活动》」），会直接进 409 的报错文案，
        所以实现方要写清楚，别只丢一个内部 id。
        """
        ...


_checker: AssetReferenceChecker | None = None


def register_asset_reference_checker(port: AssetReferenceChecker) -> None:
    """注册实现。重复注册直接报错：权威来源只能有一个。"""
    global _checker
    if _checker is not None:
        raise RuntimeError("asset 引用检查器已注册，不允许覆盖")
    _checker = port


def reset_asset_reference_checker() -> None:
    """仅测试用。"""
    global _checker
    _checker = None


def has_asset_reference_checker() -> bool:
    return _checker is not None


async def ask_asset_references(asset_ids: Sequence[str]) -> Mapping[str, Sequence[str]]:
    """问「谁在引用」。未接上实现时返回空映射（见模块文档）。"""
    if _checker is None or not asset_ids:
        return {}
    return await _checker.references(asset_ids)
