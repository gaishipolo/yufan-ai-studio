"""「这个素材有没有被引用」的注入口。

素材删除要先问一句「还有谁在用它」。答案在**别的模块**手里（模板、分镜、镜头），
而业务模块之间不许互相 import —— 所以走注入口。

未接上时返回空映射，即「没人引用、可以删」。这是刻意的中性默认：本仓库目前
还没有任何模块把素材 id 写进自己的表（模板用的是归档路径），接上实现的是
将来那个真的会记录引用的模块。反过来默认「可能有引用、一律拒绝」会让删除
功能在实现接上之前完全不可用，而用户看得见的只有「删不掉」。
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
