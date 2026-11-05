"""身份服务：认证、权限展开、用户与角色的业务规则。

三条红线（都写在这里，不散落到 router）：
1. 不能停用/删除自己 —— 手滑一点就把自己锁在门外。
2. 系统必须保留至少一个启用的超级管理员 —— 否则没人能再改权限，只能改数据库。
3. 内置角色不可删除、`code` 不可改 —— 但权限内容可调，运营需要。

红线的实现方式统一为「**先改、flush、再数一遍**」：如果改完超管归零就抛错，
事务由 `get_session` 依赖回滚。这样不用为每种操作手写「这次改动会不会导致锁死」
的推演逻辑——数一遍最可靠。
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.context import SUPER_PERMISSION, CurrentUser
from core.logging import get_logger
from core.permissions import catalog
from core.response import Page, PageParams
from core.security import create_access_token, hash_password, verify_password
from db.base import utcnow
from db.session import get_session_factory
from modules.identity import throttle
from modules.identity.errors import (
    AccountDisabled,
    BuiltinRoleLocked,
    CannotTargetSelf,
    CredentialInvalid,
    LastSuperuser,
    OldPasswordMismatch,
    PermissionEscalation,
    RoleCodeTaken,
    RoleInUse,
    RoleNotFound,
    SuperuserRequired,
    UnknownPermission,
    UsernameTaken,
    UserNotFound,
)
from modules.identity.models import Role, User
from modules.identity.permissions import BUILTIN_ROLES, SUPERUSER_ROLE
from modules.identity.repository import RoleRepository, UserRepository
from modules.identity.schema import (
    MeOut,
    PasswordChangeIn,
    PermissionCatalogOut,
    PermissionGroupOut,
    PermissionOut,
    ProfileUpdateIn,
    RoleBriefOut,
    RoleCreateIn,
    RoleOut,
    RoleUpdateIn,
    TokenOut,
    UserCreateIn,
    UserOut,
    UserUpdateIn,
)

logger = get_logger(__name__)

# 用户不存在时也要跑一次哈希校验，否则「响应快 = 用户名不存在」就是一个免费的账号枚举接口
_DUMMY_HASH: str | None = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(16))
    return _DUMMY_HASH


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    """一次解析的完整结果：用户 + 角色 + 展开后的权限码。"""

    user: User
    roles: tuple[Role, ...]
    permissions: frozenset[str]

    @property
    def role_codes(self) -> tuple[str, ...]:
        return tuple(role.code for role in self.roles)

    @property
    def is_superuser(self) -> bool:
        return SUPER_PERMISSION in self.permissions


def _brief(role: Role) -> RoleBriefOut:
    return RoleBriefOut(
        id=role.id, code=role.code, name=role.name, is_superuser=role.is_superuser
    )


def _user_fields(user: User, roles: Sequence[Role]) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "avatar_url": user.avatar_url,
        "is_active": user.is_active,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
        "roles": [_brief(role) for role in roles],
    }


def _known_permission(code: str, known: frozenset[str], resources: set[str]) -> bool:
    """权限码是否可被识别。

    除 catalog 里的精确码外，还接受 `资源:*` 与 `*` —— 通配是「这个资源未来新增的
    动作也一起授予」的合法意图，不能当成拼错的垃圾数据。
    """
    if code == SUPER_PERMISSION:
        return True
    if code in known:
        return True
    return code.endswith(":*") and code[:-2] in resources


class IdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.roles = RoleRepository(session)

    # ------------------------------------------------------------------ #
    # 认证 / 解析
    # ------------------------------------------------------------------ #
    async def authenticate(
        self, username: str, password: str, *, client_ip: str = ""
    ) -> User:
        """校验口令并记录登录时间。

        `username` 已在 `LoginIn` 里归一成小写；这里再 strip 一次，
        防止内部调用（CLI、脚本）绕过 schema 直接传脏值。

        失败计数放在 `verify_password` **之后**、抛 `CredentialInvalid` **之前**：
        先算再记，失败路径的耗时与成功路径一致，不会把「账号存在」从时间维泄漏出去。
        """
        key = f"{username}|{client_ip}"
        throttle.guard(key)

        user = await self.users.get_by_username(username)
        ok = verify_password(
            password, user.password_hash if user is not None else _dummy_hash()
        )
        if user is None or not ok:
            throttle.record_failure(key)
            raise CredentialInvalid()
        throttle.reset(key)
        if not user.is_active:
            raise AccountDisabled()

        user.last_login_at = utcnow()
        await self.session.commit()
        return user

    async def resolve_identity(self, user_id: str) -> ResolvedIdentity | None:
        """用户 id → 权威身份（角色 + 权限）。账号不存在返回 None。"""
        user = await self.users.get(user_id)
        if user is None:
            return None

        roles = tuple((await self.users.roles_for([user.id])).get(user.id, ()))
        if any(role.is_superuser for role in roles):
            # 超管直接给 `*`：新模块上线、新权限点声明后自动生效，不需要逐条授权
            return ResolvedIdentity(user, roles, frozenset({SUPER_PERMISSION}))

        perms = await self.roles.permissions_for([role.id for role in roles])
        codes: set[str] = set()
        for role in roles:
            codes.update(perms.get(role.id, ()))
        return ResolvedIdentity(user, roles, frozenset(codes))

    async def build_token(self, user: User, identity: ResolvedIdentity) -> TokenOut:
        token = create_access_token(
            user.id,
            name=user.display_name or user.username,
            roles=list(identity.role_codes),
            # 令牌里的 perms 只作调试参考：权威来源是请求期的 resolve_principal
            permissions=sorted(identity.permissions),
        )
        return TokenOut(
            access_token=token,
            token_type="Bearer",
            expires_in=settings.access_token_ttl_seconds,
            user=self._to_me(identity),
        )

    @staticmethod
    def _to_me(identity: ResolvedIdentity) -> MeOut:
        return MeOut(
            **_user_fields(identity.user, identity.roles),
            permissions=sorted(identity.permissions),
        )

    async def me(self, user_id: str) -> MeOut:
        identity = await self.resolve_identity(user_id)
        if identity is None:
            raise UserNotFound()
        return self._to_me(identity)

    # ------------------------------------------------------------------ #
    # 自助
    # ------------------------------------------------------------------ #
    async def update_me(self, user_id: str, payload: ProfileUpdateIn) -> MeOut:
        """自助改资料：**只允许改展示字段**。

        这里刻意不接 `UserUpdateIn`（那是管理员用的，含 `is_active`）：
        自助路径若能把 `is_active` 改成 false，用户点错一次就把自己锁在门外，
        而恢复只有管理员一个入口。字段白名单比 `setattr` 循环更难写错——
        将来给 `UserUpdateIn` 加字段（比如 `role_ids`）也不会自动漏进自助口子。
        """
        user = await self._require_user(user_id)
        fields = payload.model_dump(exclude_unset=True)
        for field in ("display_name", "email", "avatar_url"):
            if field in fields:
                setattr(user, field, fields[field])
        await self.session.commit()
        return await self.me(user.id)

    async def change_password(self, user_id: str, payload: PasswordChangeIn) -> None:
        user = await self._require_user(user_id)
        if not verify_password(payload.old_password, user.password_hash):
            raise OldPasswordMismatch()
        user.password_hash = hash_password(payload.new_password)
        # 改密即失效该账号此前签发的全部令牌（解析器比对 iat）
        user.password_changed_at = utcnow()
        await self.session.commit()

    # ------------------------------------------------------------------ #
    # 用户管理
    # ------------------------------------------------------------------ #
    async def list_users(
        self,
        params: PageParams,
        *,
        keyword: str = "",
        is_active: bool | None = None,
        role_id: str | None = None,
    ) -> Page[UserOut]:
        rows, total = await self.users.page(
            params, keyword=keyword, is_active=is_active, role_id=role_id
        )
        roles_map = await self.users.roles_for([row.id for row in rows])
        items = [
            UserOut(**_user_fields(row, roles_map.get(row.id, ()))) for row in rows
        ]
        return Page[UserOut](
            items=items,
            total=total,
            page=params.page,
            page_size=params.page_size,
        )

    async def get_user(self, user_id: str) -> UserOut:
        user = await self._require_user(user_id)
        roles = (await self.users.roles_for([user.id])).get(user.id, ())
        return UserOut(**_user_fields(user, roles))

    async def create_user(self, payload: UserCreateIn, actor: CurrentUser) -> UserOut:
        if await self.users.get_by_username(payload.username) is not None:
            raise UsernameTaken()

        roles = await self._roles_by_ids(payload.role_ids)
        # 建号时直接挂超管角色 = 造一个自己登进去就是超管的账号，
        # 必须和 set_user_roles 走同一道闸（R4-11 路径 C）
        await self._guard_role_grant(roles, actor)
        user = User(
            username=payload.username,
            password_hash=hash_password(payload.password),
            display_name=payload.display_name or payload.username,
            email=payload.email,
            avatar_url=payload.avatar_url,
            is_active=True,
        )
        self.session.add(user)
        await self.session.flush()
        await self.users.set_roles(user.id, [role.id for role in roles])
        await self.session.commit()

        logger.info("创建用户 %s（%s）", user.username, user.id)
        return UserOut(**_user_fields(user, roles))

    async def update_user(
        self, user_id: str, payload: UserUpdateIn, actor: CurrentUser
    ) -> UserOut:
        user = await self._require_user(user_id)
        fields = payload.model_dump(exclude_unset=True)

        # `is_active` 两个方向都要处理：只写 False 分支会导致「停用后无法恢复」
        if "is_active" in fields:
            active = bool(fields["is_active"])
            if not active and user.id == actor.id:
                raise CannotTargetSelf()
            if not active:
                # 停用超管 = 让对方下不来台，与「重置超管密码」同级，需要超管
                await self._guard_target_not_above(user, actor)
            user.is_active = active

        for field in ("display_name", "email", "avatar_url"):
            if field in fields:
                setattr(user, field, fields[field])

        await self.session.flush()
        if fields.get("is_active") is False:
            await self._guard_has_superuser()
        await self.session.commit()
        return await self.get_user(user.id)

    async def set_user_roles(
        self, user_id: str, role_ids: Sequence[str], actor: CurrentUser
    ) -> UserOut:
        user = await self._require_user(user_id)
        roles = await self._roles_by_ids(role_ids)

        current = (await self.users.roles_for([user.id])).get(user.id, [])
        touches_superuser = any(role.is_superuser for role in roles) or any(
            role.is_superuser for role in current
        )
        # 授超管和撤超管都是提权面：只有超管能做，否则「能改角色」等于「能给自己发超管」
        if touches_superuser and not actor.has(SUPER_PERMISSION):
            raise SuperuserRequired()
        # 非超管角色同样要挡：给自己挂一个含 `task:*` 的角色，等于绕过「只读」授权
        await self._guard_role_grant(roles, actor)

        await self.users.set_roles(user.id, [role.id for role in roles])
        await self._guard_has_superuser()
        await self.session.commit()

        logger.info("调整用户角色 %s -> %s", user.username, [role.code for role in roles])
        return UserOut(**_user_fields(user, roles))

    async def reset_password(
        self, user_id: str, new_password: str, actor: CurrentUser
    ) -> None:
        """管理员重置口令。

        这是**最强的提权面**：不需要对方配合，重置完直接以对方身份登录。
        所以目标账号的权限必须不高于操作者——否则 `identity:update` 就等于 `*`
        （R4-11 路径 B）。
        """
        user = await self._require_user(user_id)
        await self._guard_target_not_above(user, actor)
        user.password_hash = hash_password(new_password)
        # 重置口令同样让该账号此前的令牌全部失效
        user.password_changed_at = utcnow()
        await self.session.commit()
        logger.info("管理员 %s 重置了 %s 的密码", actor.id, user.username)

    async def deactivate_user(self, user_id: str, actor: CurrentUser) -> None:
        """`DELETE /users/{id}` 的真实语义：停用，不做物理删除。

        历史任务的 `author_id` 还指着他，删掉行会让「谁做的」变成悬空引用。
        """
        user = await self._require_user(user_id)
        if user.id == actor.id:
            raise CannotTargetSelf()
        await self._guard_target_not_above(user, actor)
        user.is_active = False
        await self.session.flush()
        await self._guard_has_superuser()
        await self.session.commit()
        logger.info("停用用户 %s", user.username)

    # ------------------------------------------------------------------ #
    # 角色管理
    # ------------------------------------------------------------------ #
    async def list_roles(self) -> list[RoleOut]:
        roles = list(await self.roles.list_all())
        ids = [role.id for role in roles]
        perms = await self.roles.permissions_for(ids)
        counts = await self.roles.user_counts(ids)
        known = catalog.codes()
        resources = {code.split(":", 1)[0] for code in known}

        out: list[RoleOut] = []
        for role in roles:
            codes = perms.get(role.id, [])
            out.append(
                RoleOut(
                    id=role.id,
                    code=role.code,
                    name=role.name,
                    description=role.description,
                    is_superuser=role.is_superuser,
                    is_builtin=role.is_builtin,
                    sort_order=role.sort_order,
                    created_at=role.created_at,
                    permissions=codes,
                    user_count=counts.get(role.id, 0),
                    orphaned_permissions=[
                        code
                        for code in codes
                        if not _known_permission(code, known, resources)
                    ],
                )
            )
        return out

    async def get_role(self, role_id: str) -> RoleOut:
        role = await self._require_role(role_id)
        for item in await self.list_roles():
            if item.id == role.id:
                return item
        raise RoleNotFound()  # pragma: no cover - list_roles 必然包含该角色

    async def create_role(self, payload: RoleCreateIn, actor: CurrentUser) -> RoleOut:
        code = payload.code.strip().lower()
        if await self.roles.get_by_code(code) is not None:
            raise RoleCodeTaken()
        if payload.is_superuser:
            self._require_superuser(actor, "创建超级管理员角色")

        self._validate_permissions(payload.permissions)
        # `*` 与 is_superuser 完全等价：只挡字段、不挡字符串，等于没挡（R4-11 路径 A）
        self._guard_permission_grant(payload.permissions, actor, what=f"角色 {code}")
        role = Role(
            code=code,
            name=payload.name,
            description=payload.description,
            is_superuser=payload.is_superuser,
            is_builtin=False,
            sort_order=100,
        )
        self.session.add(role)
        await self.session.flush()
        await self.roles.set_permissions(role.id, payload.permissions)
        await self.session.commit()
        return await self.get_role(role.id)

    async def update_role(
        self, role_id: str, payload: RoleUpdateIn, actor: CurrentUser
    ) -> RoleOut:
        role = await self._require_role(role_id)
        fields = payload.model_dump(exclude_unset=True)

        if "is_superuser" in fields and fields["is_superuser"] != role.is_superuser:
            # 改角色的超管标记 = 一次性给/撤该角色下所有人的最高权限
            if not actor.has(SUPER_PERMISSION):
                raise SuperuserRequired()
            role.is_superuser = bool(fields["is_superuser"])

        for field in ("name", "description"):
            if field in fields:
                setattr(role, field, fields[field])

        await self.session.flush()
        await self._guard_has_superuser()
        await self.session.commit()
        return await self.get_role(role.id)

    async def set_role_permissions(
        self, role_id: str, permissions: Sequence[str], actor: CurrentUser
    ) -> RoleOut:
        role = await self._require_role(role_id)
        self._validate_permissions(permissions)
        self._guard_permission_grant(permissions, actor, what=f"角色 {role.code}")
        if role.is_superuser:
            self._require_superuser(actor, "修改超级管理员角色的权限")
        await self.roles.set_permissions(role.id, permissions)
        await self.session.commit()
        return await self.get_role(role.id)

    async def delete_role(self, role_id: str) -> None:
        role = await self._require_role(role_id)
        if role.is_builtin:
            raise BuiltinRoleLocked()
        if (await self.roles.user_counts([role.id])).get(role.id, 0) > 0:
            raise RoleInUse()

        await self.roles.drop_bindings(role.id)
        await self.session.delete(role)
        await self.session.flush()
        await self._guard_has_superuser()
        await self.session.commit()

    # ------------------------------------------------------------------ #
    # 权限目录
    # ------------------------------------------------------------------ #
    def permission_catalog(self) -> PermissionCatalogOut:
        groups = [
            PermissionGroupOut(
                group=group,
                items=[
                    PermissionOut(code=s.code, name=s.name, description=s.description)
                    for s in items
                ],
            )
            for group, items in catalog.grouped().items()
        ]
        return PermissionCatalogOut(groups=groups, total=len(catalog))

    # ------------------------------------------------------------------ #
    # 启动引导
    # ------------------------------------------------------------------ #
    async def ensure_builtin_roles(self) -> int:
        """确保内置角色存在，返回新建数量。

        只补「角色不存在」这一种情况，**不覆盖已有角色的权限**：
        管理员手工调过的配置不该在下次重启时被悄悄改回去。
        代价是新增模块的权限点不会自动进内置角色——这是安全的默认值（宁缺勿滥），
        在角色界面勾一下即可。
        """
        created = 0
        for spec in BUILTIN_ROLES:
            if await self.roles.get_by_code(spec.code) is not None:
                continue
            role = Role(
                code=spec.code,
                name=spec.name,
                description=spec.description,
                is_superuser=spec.is_superuser,
                is_builtin=True,
                sort_order=spec.sort_order,
            )
            self.session.add(role)
            await self.session.flush()
            await self.roles.set_permissions(role.id, spec.permissions)
            created += 1
        if created:
            await self.session.commit()
            logger.info("内置角色已初始化：%d 个", created)
        return created

    async def ensure_superuser(
        self, username: str, password: str, display_name: str = ""
    ) -> tuple[User, bool]:
        """创建或修复一个超级管理员（`app create-admin` 用）。

        账号已存在时只做「重置密码 + 确保有超管角色」，不新建——
        运维手滑重跑命令不该造出第二个同名账号。
        """
        await self.ensure_builtin_roles()
        role = await self.roles.get_by_code(SUPERUSER_ROLE)
        if role is None:  # pragma: no cover - ensure_builtin_roles 刚建过
            raise RoleNotFound("缺少内置角色 admin")

        user = await self.users.get_by_username(username)
        created = user is None
        if user is None:
            user = User(
                username=username.strip().lower(),
                password_hash=hash_password(password),
                display_name=display_name or username,
                is_active=True,
            )
            self.session.add(user)
            await self.session.flush()
        else:
            user.password_hash = hash_password(password)
            user.is_active = True

        current = set(await self.users.role_ids_of(user.id))
        if role.id not in current:
            await self.users.set_roles(user.id, [*current, role.id])
        await self.session.commit()
        return user, created

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    async def _require_user(self, user_id: str) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise UserNotFound()
        return user

    async def _require_role(self, role_id: str) -> Role:
        role = await self.roles.get(role_id)
        if role is None:
            raise RoleNotFound()
        return role

    async def _roles_by_ids(self, role_ids: Sequence[str]) -> list[Role]:
        if not role_ids:
            return []
        unique = list(dict.fromkeys(role_ids))
        roles = [await self._require_role(role_id) for role_id in unique]
        return roles

    def _validate_permissions(self, codes: Sequence[str]) -> None:
        known = catalog.codes()
        resources = {code.split(":", 1)[0] for code in known}
        invalid = [code for code in codes if not _known_permission(code, known, resources)]
        if invalid:
            raise UnknownPermission(
                f"存在无法识别的权限点：{', '.join(sorted(set(invalid)))}",
                details={"invalid": sorted(set(invalid))},
            )

    # ---------- 提权面（四个入口共用，别再各写一遍） ---------- #
    @staticmethod
    def _require_superuser(actor: CurrentUser, reason: str) -> None:
        if not actor.has(SUPER_PERMISSION):
            raise SuperuserRequired(f"{reason}：只有超级管理员可以操作")

    @staticmethod
    def _missing_grants(actor: CurrentUser, codes: Iterable[str]) -> list[str]:
        """`codes` 里调用者自己没有的那些。

        `CurrentUser.has` 已经处理 `*` 与 `资源:*` 两种通配，这里直接复用——
        另写一套通配匹配，迟早会和鉴权主路径对不上。
        """
        return sorted({code for code in codes if not actor.has(code)})

    def _guard_permission_grant(
        self, codes: Sequence[str], actor: CurrentUser, *, what: str
    ) -> None:
        missing = self._missing_grants(actor, codes)
        if missing:
            raise PermissionEscalation(
                f"不能给{what}授予自己不具备的权限",
                details={"missing": missing},
            )

    async def _guard_role_grant(
        self, roles: Sequence[Role], actor: CurrentUser
    ) -> None:
        """分配角色前的统一闸口：超管角色要超管，其余角色的权限必须是自己的子集。

        子集规则同时挡住两条路：给自己挂含 `*` 的角色（提权），
        以及把 `editor`（含 `task:*`）发给别人而自己只有 `task:read`（借刀）。
        """
        if not roles:
            return
        perms = await self.roles.permissions_for([role.id for role in roles])
        for role in roles:
            if role.is_superuser:
                self._require_superuser(actor, f"分配超级管理员角色 {role.code}")
                continue
            self._guard_permission_grant(
                perms.get(role.id, ()), actor, what=f"角色 {role.code}"
            )

    async def _guard_target_not_above(self, target: User, actor: CurrentUser) -> None:
        """不能对「权限高于自己」的账号做写操作（重置口令 / 停用）。

        否则 `identity:update` 与 `identity:delete` 就成了接管超管的两条捷径。
        """
        identity = await self.resolve_identity(target.id)
        if identity is None:  # pragma: no cover - 调用前已确认用户存在
            return
        if identity.is_superuser:
            self._require_superuser(actor, f"操作超级管理员账号 {target.username}")
            return
        missing = self._missing_grants(actor, identity.permissions)
        if missing:
            raise PermissionEscalation(
                f"不能操作权限高于自己的账号 {target.username}",
                details={"missing": missing},
            )

    async def _guard_has_superuser(self) -> None:
        """改动生效后（flush 已写入事务）再数一遍超管，归零就抛错。

        这样不用为每种操作推演「这次会不会锁死」，也不会漏掉间接路径
        （改角色标记、换角色、停用账号都会走到同一条判断）。
        """
        if await self.users.count_active_superusers() == 0:
            raise LastSuperuser()


async def bootstrap() -> None:
    """模块启动钩子：建内置角色。表不存在时明确报错，而不是静默继续。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            await IdentityService(session).ensure_builtin_roles()
        except SQLAlchemyError as exc:
            raise RuntimeError(
                "内置角色初始化失败：身份相关数据表可能尚未创建。"
                "生产环境请先执行 `alembic upgrade head`；开发环境检查 DB_URL。"
            ) from exc


__all__ = ["IdentityService", "ResolvedIdentity", "bootstrap"]
