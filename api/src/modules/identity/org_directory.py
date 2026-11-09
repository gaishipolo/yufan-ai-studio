"""`OrgDirectory` 注入口的身份模块实现。

资产库的「小组素材库」要按组成员关系过滤，但它不能 import 本模块。这里把
两个查询包成注入口实现，注册在 `modules/identity/__init__.py` 的 import 期。

为什么直接用 `GroupMember` / `Group` 而不是走 `IdentityService`：这个口子只回答
两个纯数据问题，不涉及授权、不需要审计、也不能触发「改权限」这类副作用。
绕一层 service 只会让「查询」和「变更」的边界变模糊。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.identity.models import Group, GroupMember, User

__all__ = ["IdentityOrgDirectory"]


class IdentityOrgDirectory:
    async def group_ids_of(self, session: AsyncSession, user_id: str) -> Sequence[str]:
        return list(
            await session.scalars(
                select(GroupMember.group_id).where(GroupMember.user_id == user_id)
            )
        )

    async def group_names(
        self, session: AsyncSession, group_ids: Sequence[str]
    ) -> Mapping[str, str]:
        wanted = [group_id for group_id in dict.fromkeys(group_ids) if group_id]
        if not wanted:
            return {}
        rows = await session.execute(
            select(Group.id, Group.name).where(Group.id.in_(wanted))
        )
        return {group_id: name for group_id, name in rows.all()}

    async def user_names(
        self, session: AsyncSession, user_ids: Sequence[str]
    ) -> Mapping[str, str]:
        wanted = [user_id for user_id in dict.fromkeys(user_ids) if user_id]
        if not wanted:
            return {}
        rows = await session.execute(
            select(User.id, User.display_name, User.username).where(User.id.in_(wanted))
        )
        # 显示名可能为空（早期账号），退回用户名——宁可显示 `zhangsan`，
        # 也别在前端出现一串 `u_7f3a9c`。
        return {
            user_id: (display_name or username)
            for user_id, display_name, username in rows.all()
        }
