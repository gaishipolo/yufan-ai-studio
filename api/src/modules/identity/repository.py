"""身份模块仓储：把 SQL 关在这一层。

列表页刻意做成「批量取角色」而不是逐行查询：用户列表一页 24 条，
逐行 `roles_of` 就是 24 次往返，而这种 N+1 在本地 sqlite 上感觉不出来，
上了真实数据库才会变成首屏卡顿。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import SUPER_PERMISSION
from core.response import PageParams
from db.base import utcnow
from db.repository import BaseRepository
from modules.identity.models import (
    Group,
    GroupMember,
    LoginEvent,
    PermissionGrant,
    Role,
    RolePermission,
    User,
    UserRole,
    UserSession,
)
from modules.identity.permissions import (
    EFFECT_ALLOW,
    EFFECT_DENY,
    SCOPE_GROUP,
    SCOPE_TEAM,
    SCOPE_USER,
    TEAM_SCOPE_ID,
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
        name: str = "",
        username: str = "",
        phone: str = "",
        is_active: bool | None = None,
        role_id: str | None = None,
        group_id: str | None = None,
    ) -> tuple[Sequence[User], int]:
        """用户页筛选。

        `keyword` 是旧版「一个框搜所有」；`name` / `username` / `phone` 是
        新版三个独立输入框。两套都留着：老前端还在传 keyword，直接删会让
        「搜索」在发布窗口里静默失效。多个条件同时传时取交集。
        """
        stmt = select(User)
        if keyword:
            like = f"%{keyword.strip()}%"
            stmt = stmt.where(
                or_(
                    User.username.ilike(like),
                    User.display_name.ilike(like),
                    User.email.ilike(like),
                    User.phone.ilike(like),
                    User.position.ilike(like),
                )
            )
        if name.strip():
            stmt = stmt.where(User.display_name.ilike(f"%{name.strip()}%"))
        if username.strip():
            stmt = stmt.where(User.username.ilike(f"%{username.strip()}%"))
        if phone.strip():
            stmt = stmt.where(User.phone.ilike(f"%{phone.strip()}%"))
        if is_active is not None:
            stmt = stmt.where(User.is_active.is_(is_active))
        if role_id:
            stmt = stmt.where(
                User.id.in_(select(UserRole.user_id).where(UserRole.role_id == role_id))
            )
        if group_id:
            stmt = stmt.where(
                User.id.in_(
                    select(GroupMember.user_id).where(GroupMember.group_id == group_id)
                )
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

    async def get_many(self, user_ids: Sequence[str]) -> list[User]:
        """一次取多个用户，供「整组替换成员」校验 id 是否真实存在。"""
        if not user_ids:
            return []
        return list(
            await self.session.scalars(
                select(User).where(User.id.in_(list(dict.fromkeys(user_ids))))
            )
        )

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

    async def active_superuser_ids(self) -> set[str]:
        """启用账号里「超管等价」的用户 id。

        判定必须与 `IdentityService.resolve_identity` **同源**：持有 `is_superuser`
        角色，或者在任意层（角色权限 / 个人授权 / 小组授权）拿到 `*`。

        为什么不能只数角色：`resolve_identity` 的注释写得很清楚——「任一层出现 `*`
        都等价于超管：只认角色上的 `is_superuser` 会让『给某人单独授 `*`』这条合法
        操作失效」。同一个系统里若两处口径不同，「最后一个超管」就会数出 0 ——
        管理面所有写操作都开始报 `LastSuperuser`，而真正的原因是判定标准选错了。

        三次查询而不是一次巨型 join：`permission_grants` 的 group 层要经过
        `group_members` 才能落到用户，硬拼成一条 SQL 会得到三层嵌套 join 加 OR，
        可读性换不来性能 —— 这个方法只在改权限时调用，不在请求主路径上。
        """
        active = User.is_active.is_(True)
        ids: set[str] = set()

        # ① is_superuser 角色，或角色权限里直接写了 `*`（两者等价）
        role_rows = await self.session.scalars(
            select(User.id)
            .distinct()
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .outerjoin(RolePermission, RolePermission.role_id == Role.id)
            .where(
                active,
                or_(
                    Role.is_superuser.is_(True),
                    RolePermission.permission == SUPER_PERMISSION,
                ),
            )
        )
        ids.update(role_rows)

        # ② 个人授权 `*`
        personal = await self.session.scalars(
            select(PermissionGrant.scope_id)
            .join(User, User.id == PermissionGrant.scope_id)
            .where(
                active,
                PermissionGrant.scope == SCOPE_USER,
                PermissionGrant.permission == SUPER_PERMISSION,
            )
        )
        ids.update(personal)

        # ③ 所属小组的授权 `*`（团队层不许放 `*`，写接口已经拦了，这里不必查）
        grouped = await self.session.scalars(
            select(GroupMember.user_id)
            .join(User, User.id == GroupMember.user_id)
            .join(
                PermissionGrant,
                and_(
                    PermissionGrant.scope == SCOPE_GROUP,
                    PermissionGrant.scope_id == GroupMember.group_id,
                ),
            )
            .where(active, PermissionGrant.permission == SUPER_PERMISSION)
        )
        ids.update(grouped)
        return set(ids)

    async def count_active_superusers(self, *, exclude_user_id: str | None = None) -> int:
        """启用的超级管理员人数（用于「不能锁死自己」的红线）。

        「超管」的定义与鉴权侧同源，见 `active_superuser_ids`。
        """
        ids = await self.active_superuser_ids()
        if exclude_user_id:
            ids.discard(exclude_user_id)
        return len(ids)


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

    async def permission_codes(self) -> set[str]:
        """全表出现过的权限码（启动时清理下线码用）。"""
        return set(
            await self.session.scalars(select(RolePermission.permission).distinct())
        )

    async def drop_permission_codes(self, codes: Sequence[str]) -> int:
        """删掉这些码的全部分绑定，返回删掉的行数。"""
        if not codes:
            return 0
        result = cast(
            "CursorResult[Any]",
            await self.session.execute(
                delete(RolePermission).where(
                    RolePermission.permission.in_(list(codes))
                )
            ),
        )
        await self.session.flush()
        return int(result.rowcount or 0)

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


@dataclass(frozen=True, slots=True)
class LayerCodes:
    """一个用户的三层授权明细。

    `groups` 的键是 group_id：UI 要展示「这条权限来自哪个小组」，
    只给一个合并后的集合就说不清来源了。`all_codes()` 才是鉴权用的合并结果。
    """

    team: tuple[str, ...] = ()
    groups: dict[str, tuple[str, ...]] | None = None
    personal: tuple[str, ...] = ()
    #: 个人层的**例外**（`effect='deny'`）。`all_codes()` 刻意不含它 ——
    #: 那个方法是「他拥有什么」，否决是「他额外不要什么」，两个方向不能混。
    denies: tuple[str, ...] = ()
    group_ids: tuple[str, ...] = ()

    def all_codes(self) -> set[str]:
        codes = set(self.team) | set(self.personal)
        for group_codes in (self.groups or {}).values():
            codes.update(group_codes)
        return codes


class PermissionLayerRepository:
    """小组 / 成员 / 三层授权。**没有外键，删除必须显式清绑定**。

    `layer_codes()` 是每请求的热路径：一次取该用户的组 id，再一次把
    team / group / user 三层的 grant 全取回来。分成三次查询会让
    `resolve_principal` 每请求多两次往返，而这里没有缓存可以兜底。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------- 小组 ----------
    async def list_groups(self) -> Sequence[Group]:
        return (
            await self.session.scalars(
                select(Group).order_by(Group.created_at, Group.name)
            )
        ).all()

    async def get_group(self, group_id: str) -> Group | None:
        return await self.session.get(Group, group_id)

    async def get_group_by_name(self, name: str) -> Group | None:
        return await self.session.scalar(select(Group).where(Group.name == name))

    async def groups_by_ids(self, group_ids: Sequence[str]) -> dict[str, Group]:
        if not group_ids:
            return {}
        rows = await self.session.scalars(
            select(Group).where(Group.id.in_(list(group_ids)))
        )
        return {group.id: group for group in rows.all()}

    async def create_group(self, name: str, description: str = "") -> Group:
        group = Group(name=name, description=description)
        self.session.add(group)
        await self.session.flush()
        return group

    async def delete_group(self, group_id: str) -> None:
        """删组 = 删成员 + 删该组的 grant + 删组，三步在同一个事务里。

        少了第二步会留下「没有任何界面能看到、却仍然能授予权限」的孤儿行；
        组 id 是 uuid，不会被复用，但它会让 doctor 对账永远报脏。
        """
        await self.session.execute(
            delete(GroupMember).where(GroupMember.group_id == group_id)
        )
        await self.session.execute(
            delete(PermissionGrant).where(
                PermissionGrant.scope == SCOPE_GROUP,
                PermissionGrant.scope_id == group_id,
            )
        )
        await self.session.execute(delete(Group).where(Group.id == group_id))
        await self.session.flush()

    async def set_members(self, group_id: str, user_ids: Sequence[str]) -> None:
        """整组替换。先清后插，与 `UserRepository.set_roles` 同一套语义。"""
        await self.session.execute(
            delete(GroupMember).where(GroupMember.group_id == group_id)
        )
        for user_id in dict.fromkeys(user_ids):
            self.session.add(GroupMember(group_id=group_id, user_id=user_id))
        await self.session.flush()

    async def add_to_groups(self, user_id: str, group_ids: Sequence[str]) -> None:
        """建号时把新账号挂进若干小组。

        只在「新建用户」路径调用：此刻 `user_id` 不可能已属于任何组，
        所以是纯插入，不需要像 `set_members` 那样先清后插。小组的存在性由
        service 在这一步之前用 `groups_by_ids()` 校验过。
        """
        if not group_ids:
            return
        self.session.add_all(
            [
                GroupMember(group_id=gid, user_id=user_id)
                for gid in dict.fromkeys(group_ids)
            ]
        )
        await self.session.flush()

    async def group_ids_for_user(self, user_id: str) -> list[str]:
        return list(
            await self.session.scalars(
                select(GroupMember.group_id).where(GroupMember.user_id == user_id)
            )
        )

    async def members_for(self, group_ids: Sequence[str]) -> dict[str, list[str]]:
        if not group_ids:
            return {}
        rows = await self.session.execute(
            select(GroupMember.group_id, GroupMember.user_id).where(
                GroupMember.group_id.in_(list(group_ids))
            )
        )
        result: dict[str, list[str]] = {gid: [] for gid in group_ids}
        for group_id, user_id in rows.all():
            result.setdefault(group_id, []).append(user_id)
        return result

    async def member_counts(self, group_ids: Sequence[str]) -> dict[str, int]:
        if not group_ids:
            return {}
        rows = await self.session.execute(
            select(GroupMember.group_id, func.count())
            .where(GroupMember.group_id.in_(list(group_ids)))
            .group_by(GroupMember.group_id)
        )
        counts = {gid: 0 for gid in group_ids}
        counts.update({gid: int(n) for gid, n in rows.all()})
        return counts

    # ---------- 授权 ----------
    async def grants(
        self, scope: str, scope_id: str, *, effect: str = EFFECT_ALLOW
    ) -> list[str]:
        """某一层授予（或否决）的权限码。

        `effect` 默认只看 `allow`：调用方问「他现在被给了什么」时，
        混进否决项会让它以为有这项权限。要拿否决请显式传 `EFFECT_DENY`。
        """
        return list(
            await self.session.scalars(
                select(PermissionGrant.permission)
                .where(
                    PermissionGrant.scope == scope,
                    PermissionGrant.scope_id == scope_id,
                    PermissionGrant.effect == effect,
                )
                .order_by(PermissionGrant.permission)
            )
        )

    async def set_grants(
        self,
        scope: str,
        scope_id: str,
        codes: Sequence[str],
        *,
        effect: str = EFFECT_ALLOW,
    ) -> None:
        """整组替换某一层的授予 / 否决。

        **`delete` 必须带 `effect` 条件**：不带的话，改一次允许项就会把这个人
        的例外一起删掉 —— 界面上只表现为「权限多了一项」，没人会怀疑是保存
        动作弄丢的（这类静默丢失本仓已经踩过两次）。
        """
        # 结构性的不对称：例外只可能出现在个人层。写在这里而不是靠调用方自觉 ——
        # 「只有最具体的一层能否决」是加新层时不重新决定优先级的**前提**，
        # 让它变成一条运行时断言，比写在文档里可靠。
        if effect == EFFECT_DENY and scope != SCOPE_USER:
            raise ValueError(
                f"例外（deny）只能出现在个人层，收到 scope={scope!r}"
            )
        await self.session.execute(
            delete(PermissionGrant).where(
                PermissionGrant.scope == scope,
                PermissionGrant.scope_id == scope_id,
                PermissionGrant.effect == effect,
            )
        )
        for code in dict.fromkeys(codes):
            self.session.add(
                PermissionGrant(
                    scope=scope,
                    scope_id=scope_id,
                    permission=code,
                    effect=effect,
                )
            )
        await self.session.flush()

    async def layer_codes(self, user_id: str) -> LayerCodes:
        group_ids = tuple(await self.group_ids_for_user(user_id))
        rows = await self.session.execute(
            select(
                PermissionGrant.scope,
                PermissionGrant.scope_id,
                PermissionGrant.permission,
                PermissionGrant.effect,
            ).where(
                or_(
                    and_(
                        PermissionGrant.scope == SCOPE_TEAM,
                        PermissionGrant.scope_id == TEAM_SCOPE_ID,
                    ),
                    and_(
                        PermissionGrant.scope == SCOPE_GROUP,
                        PermissionGrant.scope_id.in_(list(group_ids)),
                    ),
                    and_(
                        PermissionGrant.scope == SCOPE_USER,
                        PermissionGrant.scope_id == user_id,
                    ),
                )
            )
        )
        team: list[str] = []
        groups: dict[str, list[str]] = {gid: [] for gid in group_ids}
        personal: list[str] = []
        denies: list[str] = []
        for scope, scope_id, code, effect in rows.all():
            # 否决只可能出现在个人层（写入口保证），但仍然按 effect 分流，
            # 不靠「scope 一定是 user」这个隐含前提。
            if effect == EFFECT_DENY:
                denies.append(code)
                continue
            if scope == SCOPE_TEAM:
                team.append(code)
            elif scope == SCOPE_USER:
                personal.append(code)
            elif scope == SCOPE_GROUP:
                groups.setdefault(scope_id, []).append(code)
        return LayerCodes(
            team=tuple(sorted(team)),
            groups={gid: tuple(sorted(codes)) for gid, codes in groups.items()},
            personal=tuple(sorted(personal)),
            denies=tuple(sorted(denies)),
            group_ids=group_ids,
        )

    async def all_grant_codes(self) -> set[str]:
        """doctor 对账：三层表里出现过的全部权限码。"""
        return set(
            await self.session.scalars(
                select(PermissionGrant.permission).distinct()
            )
        )

    async def all_grants(self) -> list[tuple[str, str, str]]:
        """三层表里的全部**授予**行：`(scope, scope_id, permission)`。

        给「一次性迁移」用：按 scope/scope_id 分组，才能判断某一层
        「有接口码、但一个页面码都没有」。

        **只看 `allow`**：否决行不代表这一层「拥有」这个码，把它算进来会让
        迁移以为「这个账号有接口没页面」，于是给他补一个页面权限 ——
        而那条权限本来是被他自己关掉的。
        """
        rows = await self.session.execute(
            select(
                PermissionGrant.scope,
                PermissionGrant.scope_id,
                PermissionGrant.permission,
            ).where(PermissionGrant.effect == EFFECT_ALLOW)
        )
        return [(row[0], row[1], row[2]) for row in rows.all()]

    async def drop_codes(self, codes: Sequence[str]) -> int:
        """删掉这些码在**任意层**的授权行，返回行数。

        三层一起删而不是逐层传参：调用方（启动引导）拿到的就是「目录里没有的码」，
        它落在哪一层都该清掉 —— 逐层判断只会多三个调用点、多三种漏法。
        """
        if not codes:
            return 0
        result = cast(
            "CursorResult[Any]",
            await self.session.execute(
                delete(PermissionGrant).where(
                    PermissionGrant.permission.in_(list(codes))
                )
            ),
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def orphan_grants(self, valid_group_ids: set[str]) -> list[str]:
        """doctor 对账：指向已删除小组的 grant。"""
        rows = await self.session.scalars(
            select(PermissionGrant.scope_id).where(
                PermissionGrant.scope == SCOPE_GROUP
            ).distinct()
        )
        return sorted(set(rows) - valid_group_ids)


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
