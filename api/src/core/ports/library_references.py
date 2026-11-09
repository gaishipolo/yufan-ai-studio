"""v2 资产删除前的引用检查注入口（fail-closed）。

v2 的「彻底删除」不可逆，删之前必须问一遍「还有谁在用它」。答案分散在
模板、任务、封装、下载任务手里，而业务模块之间不许互相 import——所以走注入口。

## 为什么不能只是「记得注册」

与 `core/ports/assets.py` 同一类失败方向：漏注册 → 引用检查返回空 →
删除照常成功 → 引用方从此裂图/裂视频。静默且不可逆。所以这里也是
**fail-closed**：实现未注册时 `get_library_references()` 抛 `Unavailable`(503)，
调用方必须拒绝删除，而不是当成「没人引用」。

与旧 `asset-references` 的区别：那个查 `assets.id` 的引用（`video_tasks`、
`template_pack_references`），这个查 `library_asset_id` 的引用（v2 封装成员、
v2 下载任务、以及将来接入的模块）。两套 id 空间不同，不能混用一个注入口。

## 需要声明的来源

v2 内部写 `library_asset_id` 的位置（`library_pack_items`、`library_download_items`）
由资产库模块自己实现并直接登记；跨模块来源由各自的 `ModuleSpec` 声明
（M2 起接入时补）。这条协议只负责「聚合与 fail-closed」。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import Unavailable
from core.ports.host import Port

__all__ = [
    "LibraryReferenceChecker",
    "get_library_references",
    "has_library_references",
    "library_reference_gaps",
    "register_library_references",
    "reset_library_references",
]


@runtime_checkable
class LibraryReferenceChecker(Protocol):
    """谁在引用这些 v2 资产。"""

    async def references(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]:
        """返回 `{asset_id: [给人看的引用方描述]}`，无引用的 id 可省略。

        描述会直接进 409 的文案（例如「封装《圣诞活动》」「下载任务 d_42」），
        所以写清楚，别只丢内部 id。
        """
        ...


_references: Port[LibraryReferenceChecker] = Port("library-references", multi=False)


def register_library_references(port: LibraryReferenceChecker) -> LibraryReferenceChecker:
    return _references.provide(port, owner="modules.library")


def has_library_references() -> bool:
    return _references.has()


def get_library_references() -> LibraryReferenceChecker:
    impl = _references.single()
    if impl is None:
        raise Unavailable(
            "资产库引用检查不可用：没有任何模块注册 LibraryReferenceChecker。"
            "彻底删除一律拒绝——这里不能把「查不到」当成「没人引用」。"
        )
    return impl


def library_reference_gaps() -> list[str]:
    """启动自检用：返回缺失的检查实现描述（当前只有一个来源，空列表即就绪）。"""
    return [] if has_library_references() else ["modules.library 未注册引用检查实现"]


def reset_library_references() -> None:
    """仅测试用。"""
    _references.reset()
