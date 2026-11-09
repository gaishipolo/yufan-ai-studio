"""「这个旧包/主体里有哪些图、什么顺序、能不能下载」的只读注入口。

模板在 prepare 阶段需要一组**按顺序**的参考图，顺序就是提示词里的编号顺序。
改造前它直接读 `template_pack_versions.reference_ids`；主体落地后，权威来源变成
`subject_versions.members`。两个模块不许互相 import，所以走注入口。

## 为什么返回 `None` 而不是空列表

`None` = **不知道**（没有映射、或这一版在主体那边不存在）；`[]` = **确实是空的**。
两者混在一起时，调用方会把「迁移还没跑到」当成「这个包没有图」，而症状是
生成出来的图少了参考——最难归因的一类故障。

拿不到就返回 `None`，由调用方决定回落旧路径还是报错。本仓选**回落**：
旧路径是这一版的正式读路径，回落结果与改造前逐字节一致。

## 与 `asset-lookup` 的分工

`asset-lookup` 回答「这张图是什么」（尺寸、归属、删了没有）；
`subject-lookup` 回答「这一版由哪几张图组成、顺序如何、有哪些能力」。
前者是单张的事实，后者是组合的事实。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.ports.host import Port

__all__ = [
    "SubjectSnapshot",
    "SubjectLookup",
    "get_subject_lookup",
    "has_subject_lookup",
    "register_subject_lookup",
    "reset_subject_lookup",
]


@dataclass(frozen=True, slots=True)
class SubjectSnapshot:
    """某一版主体的冻结视图。

    只带模板用得上的三样：**顺序**（编号）、**角色**（按角色取图）、**能力**
    （能不能下载原件）。多带字段的代价是模板侧会开始依赖主体内部的实现细节，
    而那份细节正是下一批要动的。
    """

    subject_id: str
    version: int
    #: 按 `sort_order` 升序的素材 id。**这个顺序就是编号顺序**，不要重排。
    asset_ids: tuple[str, ...]
    #: `{asset_id: role}`，用于模板按角色取图（front / side / detail…）
    roles: dict[str, str]
    #: 主体级能力（成员带 `in_output` 的交集）。模板据此决定是否提供"下载原件"。
    capabilities: frozenset[str]


@runtime_checkable
class SubjectLookup(Protocol):
    """按 `pack_id` + 版本取冻结成员。"""

    async def snapshot(
        self, session: AsyncSession, pack_id: str, version: int
    ) -> SubjectSnapshot | None:
        """返回该版本的主体快照；**没有映射或没有这一版时返回 `None`**。

        `session` 由调用方传入：调用方在同一个事务里先读 run、再读主体，
        两次读必须看到同一个快照，否则会出现「已发布版本与成员对不上」的窗口。
        """
        ...


_lookup: Port[SubjectLookup] = Port("subject-lookup", multi=False)


def register_subject_lookup(port: SubjectLookup) -> SubjectLookup:
    """素材模块在装配期调用一次。"""
    return _lookup.provide(port)


def has_subject_lookup() -> bool:
    return _lookup.has()


def get_subject_lookup() -> SubjectLookup | None:
    """取实现。**没有实现时返回 `None` 而不是抛错**。

    与 `get_asset_lookup` 的 fail-closed 相反，这是刻意的：主体是**新增**的
    读路径，它的缺席不该让 prepare 挂掉——旧路径仍然完整可用。
    调用方必须处理 `None`（回落旧路径），而"静默返回空图"是不允许的。
    """
    return _lookup.single()


def reset_subject_lookup() -> None:
    """仅测试用。"""
    _lookup.reset()


def member_ids_of(snapshot: SubjectSnapshot | None) -> Sequence[str]:
    """便捷取值：给调用方一个不关心 `None` 的入口，缺失时返回空序列。"""
    return snapshot.asset_ids if snapshot is not None else ()
