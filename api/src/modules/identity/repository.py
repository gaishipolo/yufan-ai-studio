"""身份模块仓储：把 SQL 关在这一层。

列表页刻意做成「批量取角色」而不是逐行查询：用户列表一页 24 条，
逐行 `roles_of` 就是 24 次往返，而这种 N+1 在本地 sqlite 上感觉不出来，
上了真实数据库才会变成首屏卡顿。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.response import PageParams
from db.base import utcnow
from db.repository import BaseRepository
from modules.identity.models import (
    LoginEvent,
    Role,
    RolePermission,
    User,
    UserRole,
    UserSession,
)


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

    async def get_many(self, role_ids: Sequence[str]) -> list[Role]:
        """一次取多个角色。

        `_roles_by_ids` 原本是逐 id `await`：5 个角色就是 5 次往返，
        而「每加一个守卫就多三轮查询」会变成习惯（R6-07）。
        顺序不保证，调用方按自己的列表重排。
        """
        if not role_ids:
            return []
        return list(
            await self.session.scalars(
                select(Role).where(Role.id.in_(list(dict.fromkeys(role_ids))))
            )
        )

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


class SessionRepository(BaseRepository[UserSession]):
    """登录会话。**每请求都会被查一次**（解析器校验 `sid`），所以只走主键。"""

    model = UserSession

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, UserSession)

    async def active_by_id(self, session_id: str, user_id: str) -> UserSession | None:
        """会话有效 = 存在 + 属于这个用户 + 未撤销 + 未过期。

        四个条件缺一不可：只比 id 会让「换个用户的 sid」通过（sid 是从令牌里读的，
        虽然是签名过的，但把「谁在用这张票」也校验一遍是免费的）；
        只比撤销时间会漏掉「7 天到了但没人来登出」的自然过期。
        """
        stmt = select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utcnow(),
        )
        return await self.session.scalar(stmt)

    async def list_for_user(
        self, user_id: str, *, only_active: bool = True, limit: int = 50
    ) -> Sequence[UserSession]:
        stmt = select(UserSession).where(UserSession.user_id == user_id)
        if only_active:
            stmt = stmt.where(
                UserSession.revoked_at.is_(None), UserSession.expires_at > utcnow()
            )
        # 排序键以主键收尾：同一秒登录两台设备的顺序必须稳定（R8-06）
        # `limit` 是契约的一部分：这张表只增不减，接口不该无上限地吐（R10-07）
        return await self.list(
            stmt.order_by(UserSession.created_at.desc(), UserSession.id).limit(limit)
        )

    async def revoke(
        self, session_id: str, *, reason: str, user_id: str | None = None
    ) -> bool:
        """吊销单个会话，返回「这次是否真的改动了」——已吊销的再吊销返回 False，
        幂等性由调用方决定怎么用（登出接口不关心，踢下线关心）。"""
        stmt = select(UserSession).where(
            UserSession.id == session_id, UserSession.revoked_at.is_(None)
        )
        if user_id is not None:
            stmt = stmt.where(UserSession.user_id == user_id)
        target = await self.session.scalar(stmt)
        if target is None:
            return False
        target.revoked_at = utcnow()
        target.revoked_reason = reason
        await self.session.flush()
        return True

    async def revoke_all(
        self, user_id: str, *, reason: str, keep_session_id: str | None = None
    ) -> int:
        """吊销该用户的全部有效会话；`keep_session_id` 用于「退出其他设备」。"""
        stmt = select(UserSession).where(
            UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
        )
        if keep_session_id:
            stmt = stmt.where(UserSession.id != keep_session_id)
        rows = await self.list(stmt)
        now = utcnow()
        for row in rows:
            row.revoked_at = now
            row.revoked_reason = reason
        await self.session.flush()
        return len(rows)

    async def purge_expired(self, cutoff: datetime) -> int:
        """删掉 `expires_at` 早于 cutoff 的会话行，返回删除行数。

        只删「已经过期」的：未过期但已撤销的行还要留在 `revoked_reason` 里
        供排查（「这台设备是被踢的还是自己退的」），等它自然过期后一起清掉。
        """
        result = await self.session.execute(
            delete(UserSession).where(UserSession.expires_at < cutoff)
        )
        await self.session.flush()
        return int(getattr(result, "rowcount", 0) or 0)


class LoginEventRepository(BaseRepository[LoginEvent]):
    """登录审计。**只追加**：没有 update / delete 之外的口径，清理走 `purge_before`。"""

    model = LoginEvent

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, LoginEvent)

    async def latest_for_user(self, user_id: str, limit: int) -> Sequence[LoginEvent]:
        return await self.list(
            select(LoginEvent)
            .where(LoginEvent.user_id == user_id)
            .order_by(LoginEvent.created_at.desc(), LoginEvent.id.desc())
            .limit(limit)
        )

    async def purge_before(self, cutoff: datetime) -> int:
        """删掉 cutoff 之前的记录，返回删除行数。

        审计表只增不减会变成一个无限增长的表，而「三个月前的登录记录」在
        安全追溯上的边际价值已经很低——保留期是配置项，不是硬编码。
        """
        result = await self.session.execute(
            delete(LoginEvent).where(LoginEvent.created_at < cutoff)
        )
        await self.session.flush()
        return int(getattr(result, "rowcount", 0) or 0)
