"""身份模块仓储：把 SQL 关在这一层。

列表页刻意做成「批量取角色」而不是逐行查询：用户列表一页 24 条，
逐行 `roles_of` 就是 24 次往返，而这种 N+1 在本地 sqlite 上感觉不出来，
上了真实数据库才会变成首屏卡顿。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.response import PageParams
from db.repository import BaseRepository
from modules.identity.models import Role, RolePermission, User, UserRole


class UserRepository(BaseRepository[User]):
    model = User

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, User)

    async def get_by_username(self, username: str) -> User | None:
        return await self.session.scalar(
            select(User).where(User.username == username.strip().lower())
        )

    async def page(
        self,
        params: PageParams,
        *,
        keyword: str = "",
        is_active: bool | None = None,
        role_id: str | None = None,
    ) -> tuple[Sequence[User], int]:
        stmt = select(User)
        if keyword:
            like = f"%{keyword.strip()}%"
            stmt = stmt.where(
                or_(
                    User.username.ilike(like),
                    User.display_name.ilike(like),
                    User.email.ilike(like),
                )
            )
        if is_active is not None:
            stmt = stmt.where(User.is_active.is_(is_active))
        if role_id:
            stmt = stmt.where(
                User.id.in_(select(UserRole.user_id).where(UserRole.role_id == role_id))
            )
        stmt = stmt.order_by(User.created_at.desc(), User.id)
        return await self.paginate(stmt, params)

    async def roles_for(self, user_ids: Sequence[str]) -> dict[str, list[Role]]:
        """一次查出多个用户的角色，返回 `{user_id: [Role, ...]}`。"""
        if not user_ids:
            return {}
        rows = await self.session.execute(
            select(UserRole.user_id, Role)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_(list(user_ids)))
            .order_by(Role.sort_order, Role.code)
        )
        result: dict[str, list[Role]] = {uid: [] for uid in user_ids}
        for user_id, role in rows.all():
            result.setdefault(user_id, []).append(role)
        return result

    async def role_ids_of(self, user_id: str) -> list[str]:
        return list(
            await self.session.scalars(
                select(UserRole.role_id).where(UserRole.user_id == user_id)
            )
        )

    async def set_roles(self, user_id: str, role_ids: Sequence[str]) -> None:
        """整组替换。先清后插，避免「取消勾选」被漏掉。"""
        await self.session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        # 去重：前端重复传同一个 id 不应炸唯一约束
        for role_id in dict.fromkeys(role_ids):
            self.session.add(UserRole(user_id=user_id, role_id=role_id))
        await self.session.flush()

    async def count_active_superusers(self, *, exclude_user_id: str | None = None) -> int:
        """启用的超级管理员人数（用于「不能锁死自己」的红线）。"""
        stmt = (
            select(func.count(func.distinct(User.id)))
            .select_from(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.is_superuser.is_(True), User.is_active.is_(True))
        )
        if exclude_user_id:
            stmt = stmt.where(User.id != exclude_user_id)
        return int(await self.session.scalar(stmt) or 0)


class RoleRepository(BaseRepository[Role]):
    model = Role

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Role)

    async def get_by_code(self, code: str) -> Role | None:
        return await self.session.scalar(
            select(Role).where(Role.code == code.strip().lower())
        )

    async def list_all(self) -> Sequence[Role]:
        return (
            await self.session.scalars(
                select(Role).order_by(Role.sort_order, Role.code)
            )
        ).all()

    async def permissions_for(self, role_ids: Sequence[str]) -> dict[str, list[str]]:
        if not role_ids:
            return {}
        rows = await self.session.execute(
            select(RolePermission.role_id, RolePermission.permission)
            .where(RolePermission.role_id.in_(list(role_ids)))
            .order_by(RolePermission.permission)
        )
        result: dict[str, list[str]] = {rid: [] for rid in role_ids}
        for role_id, permission in rows.all():
            result.setdefault(role_id, []).append(permission)
        return result

    async def set_permissions(self, role_id: str, codes: Sequence[str]) -> None:
        await self.session.execute(
            delete(RolePermission).where(RolePermission.role_id == role_id)
        )
        for code in dict.fromkeys(codes):
            self.session.add(RolePermission(role_id=role_id, permission=code))
        await self.session.flush()

    async def user_counts(self, role_ids: Sequence[str]) -> dict[str, int]:
        if not role_ids:
            return {}
        rows = await self.session.execute(
            select(UserRole.role_id, func.count())
            .where(UserRole.role_id.in_(list(role_ids)))
            .group_by(UserRole.role_id)
        )
        counts = {rid: 0 for rid in role_ids}
        counts.update({role_id: int(n) for role_id, n in rows.all()})
        return counts

    async def drop_bindings(self, role_id: str) -> None:
        """删角色前清理权限绑定（没有外键级联，必须显式做）。"""
        await self.session.execute(
            delete(RolePermission).where(RolePermission.role_id == role_id)
        )
        await self.session.flush()
