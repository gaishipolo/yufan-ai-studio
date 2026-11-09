"""组织目录注入口：一个人属于哪些小组、这些小组叫什么。

## 为什么需要它

资产库 v2 的「小组素材库」要回答两个问题：

- 我属于哪些小组（决定 `visibility=group` 的资产我看不看得见）；
- 这个资产共享到的组叫什么（列表与详情要显示组名，不能显示 `g_7f3a`）。

答案在身份模块的 `group_members` / `user_groups` 里。而
`tests/test_architecture.py::test_modules_do_not_import_each_other` 明令禁止
业务模块互相 import —— 一开这个口子，`library` 就没法单独删、单独换成别的
组织体系（将来接企业微信的部门树时，改的是这里而不是资产库）。

所以做成注入口：身份模块实现并注册，资产库只认这两个方法。

## 为什么把「名称」也放进来

只给 id 的话，调用方还得自己想办法把 id 换成名字，于是要么再开一个注入口，
要么在 SQL 里 join 一张不属于自己的表。两条路都比这里多写一遍「谁拥有组名」
这个事实。**归属与名称必须来自同一个权威**，否则会出现「组已删、名字还在」
这种只在一个方向成立的状态。

## 未注册时的行为

`group_ids_of()` 返回空列表、`group_names()` 返回空字典 —— 中性值，不抛异常。

理由：小组是可选的组织能力。没有实现时正确的语义就是「这个人不在任何小组」，
于是小组素材库为空（少看见，不会多看见）；而抛异常会让「删掉身份模块」变成
「资产库整个起不来」。这两个方向里，静默少看见是安全的那个。

`app doctor` 的 `library.ports` 项会显示这条注入口是否已接上，避免「一直是空的」
被当成「本来就没建组」。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.ports.host import Port

__all__ = [
    "OrgDirectory",
    "group_ids_of",
    "group_names",
    "has_org_directory",
    "register_org_directory",
    "reset_org_directory",
    "user_names",
]


@runtime_checkable
class OrgDirectory(Protocol):
    """组织归属查询。调用方把自己的 session 传进来，实现方不另开会话。

    为什么传 session 而不是让实现自己开：可见性判定要和列表查询在**同一个
    快照**里完成。各开一个会话时，刚好在两次查询之间被移出小组的人会看到
    「按旧组过滤出来的行、按新组算出的权限」，这类竞态只在生产并发下出现。
    """

    async def group_ids_of(self, session: AsyncSession, user_id: str) -> Sequence[str]: ...

    async def group_names(
        self, session: AsyncSession, group_ids: Sequence[str]
    ) -> Mapping[str, str]:
        """`{group_id: name}`。查不到的 id 不出现在结果里（组已删）。"""
        ...

    async def user_names(
        self, session: AsyncSession, user_ids: Sequence[str]
    ) -> Mapping[str, str]:
        """`{user_id: 显示名}`。

        小组库列表要显示「这是谁上传的」，共享申请要显示申请人与审核人。
        只给 id 会逼调用方另开一条注入口，或者在自己的 SQL 里 join 别人的表。
        """
        ...


#: 单例：组织的权威来源只能有一个，两个来源同时生效时「我属于哪个组」没有正确答案。
_directory: Port[OrgDirectory] = Port("org-directory", multi=False)


def register_org_directory(impl: OrgDirectory) -> OrgDirectory:
    """注册实现（由拥有组织模型的模块在 import 期调用）。"""
    return _directory.provide(impl, owner="modules.identity")


def reset_org_directory() -> None:
    """仅测试使用：验证「没有实现时按空集处理」。"""
    _directory.reset()


def has_org_directory() -> bool:
    return _directory.has()


async def group_ids_of(session: AsyncSession, user_id: str) -> list[str]:
    """我属于哪些小组。未注册实现时返回空列表（中性值，见模块文档）。"""
    impl = _directory.single()
    if impl is None:
        return []
    return list(await impl.group_ids_of(session, user_id))


async def group_names(session: AsyncSession, group_ids: Sequence[str]) -> dict[str, str]:
    """组 id → 组名。未注册实现时返回空字典。"""
    wanted = [group_id for group_id in dict.fromkeys(group_ids) if group_id]
    if not wanted:
        return {}
    impl = _directory.single()
    if impl is None:
        return {}
    return dict(await impl.group_names(session, wanted))


async def user_names(session: AsyncSession, user_ids: Sequence[str]) -> dict[str, str]:
    """用户 id → 显示名。未注册实现时返回空字典（前端退化为只显示 id）。"""
    wanted = [user_id for user_id in dict.fromkeys(user_ids) if user_id]
    if not wanted:
        return {}
    impl = _directory.single()
    if impl is None:
        return {}
    return dict(await impl.user_names(session, wanted))
