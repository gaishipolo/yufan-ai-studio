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

import asyncio
import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core import audit
from core.authz import evaluate
from core.config import settings
from core.context import SUPER_PERMISSION, CurrentUser
from core.exceptions import Unauthorized
from core.logging import get_logger
from core.pages import PAGE_PREFIX, page_catalog
from core.permissions import PermissionSpec, catalog, expand_grants
from core.response import Page, PageParams
from core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    hash_password_async,
    password_slot,
    verify_password_async,
)
from db.app_meta import WRITE_RETRIES, Entry, read_entry, set_json
from db.base import utcnow
from db.session import get_session_factory
from modules.identity import policy, throttle
from modules.identity.errors import (
    AccessSwitchForbidden,
    AccountDisabled,
    BuiltinRoleLocked,
    CannotTargetSelf,
    CredentialInvalid,
    ExceptionLockedPermission,
    ExceptionWildcardForbidden,
    GroupNameTaken,
    GroupNotFound,
    LastSuperuser,
    OldPasswordMismatch,
    PermissionEscalation,
    RefreshTokenInvalid,
    RoleCodeTaken,
    RoleInUse,
    RoleNotFound,
    SessionNotFound,
    SuperuserGrantForbidden,
    SuperuserRequired,
    TeamGrantForbidden,
    UnknownPermission,
    UsernameTaken,
    UserNotFound,
)
from modules.identity.models import Group, LoginEvent, Role, User, UserSession
from modules.identity.permissions import (
    BUILTIN_ROLES,
    EFFECT_DENY,
    PERM_DELETE,
    SCOPE_GROUP,
    SCOPE_TEAM,
    SCOPE_USER,
    SUPERUSER_ROLE,
    TEAM_BASE_PERMISSIONS,
    TEAM_FLOOR_CODES,
    TEAM_FORBIDDEN_PREFIXES,
    TEAM_SCOPE_ID,
)
from modules.identity.repository import (
    LayerCodes,
    LoginEventRepository,
    PermissionLayerRepository,
    RoleRepository,
    SessionRepository,
    UserRepository,
)
from modules.identity.schema import (
    EffectivePermissionsOut,
    GroupCreateIn,
    GroupLayerOut,
    GroupOut,
    GroupUpdateIn,
    LoginEventListOut,
    LoginEventOut,
    MeOut,
    PasswordChangeIn,
    PasswordPolicyOut,
    PermissionCatalogOut,
    PermissionGroupOut,
    PermissionOriginOut,
    PermissionOut,
    PermissionSourceOut,
    ProfileUpdateIn,
    RoleBriefOut,
    RoleCreateIn,
    RoleLayerOut,
    RoleOut,
    RoleUpdateIn,
    SessionOut,
    TokenOut,
    UserCreateIn,
    UserOut,
    UserUpdateIn,
)

logger = get_logger(__name__)

# 资料字段白名单：管理员改他人资料、用户改自己资料，用的是同一份清单。
# 放成常量是为了让「新增字段时两个入口一起改」，而不是各自 `setattr` 循环各写一遍。
_PROFILE_FIELDS = ("display_name", "email", "avatar_url", "phone", "position")

# 但这份清单里的字段**风险等级不同**，守卫不能一刀切（R9-02）：
# - 展示字段（昵称 / 头像）：只影响渲染，要防的是「冒充比自己更有权威的人」，
#   判「目标是不是超管」就够。之前复用「目标权限 ⊆ 操作者」的严格守卫，结果是
#   一个只带 `identity:read` + `identity:update` 的委派角色连 viewer 的昵称都改不了
#   （40323）——把「能用的功能」换成了「看起来安全但没人能用」。
# - 找回入口（邮箱）：接上「邮箱找回密码 / 收验证码」之后，改它就是接管账号的第一步，
#   所以沿用「目标权限不得高于自己」的严格守卫（R7-03）。
_DISPLAY_FIELDS = ("display_name", "avatar_url", "phone", "position")
_RECOVERY_FIELDS = ("email",)

# `/me/sessions` 的返回上限。会话表只增不减，列表不截断的话，一个用了一年多的
# 账号打开「我的设备」会一次拿到几百条（R10-07）。超出的按登录时间倒序丢弃——
# 前端本来也只在弹窗里列最近几台。
SESSION_LIST_LIMIT = 50

# 权限目录快照在 `app_meta` 里的键。存的是「上一次启动时目录里有哪些权限码」，
# 用来区分「新声明的权限点」和「管理员手工删掉的旧权限点」——见 `sync_new_permissions`。
PERMISSION_SNAPSHOT_KEY = "permissions.catalog"

# 团队基础权限「只播种一次」的标记。与上面的目录快照不是一回事：
# 那个每次启动都要算差集（新声明的权限点要补进内置角色），这个只在首次
# 启动时把模板写进去，之后管理员在界面上删掉的基础权限**不该被重启加回来**。
TEAM_SNAPSHOT_KEY = "permissions.team_base"

# 页面权限一次性迁移的标记。放在 `app_meta` 里而不是「看有没有页面码」：
# 迁移只该跑一次，否则管理员后来特意关掉的页面会被每次启动重新打开。
PAGES_MIGRATION_KEY = "permissions.pages_migrated"


def _snapshot_detail(previous: list[str], target: list[str]) -> str:
    """给审计写一句人话：几个变几个、加了什么、删了什么。

    这份摘要进 `app_meta_history.detail`，是排查「谁把快照改小了」时第一眼要看的东西，
    所以不写「已更新」这种等于没写的话。
    """
    before, after = set(previous), set(target)
    added = sorted(after - before)
    dropped = sorted(before - after)
    parts = [f"{len(before)}→{len(after)} 个权限码"]
    if added:
        parts.append(f"新增 {added[:5]}{'…' if len(added) > 5 else ''}")
    if dropped:
        parts.append(f"移除 {dropped[:5]}{'…' if len(dropped) > 5 else ''}")
    return "；".join(parts)


def _grant_diff(previous: Sequence[str], target: Sequence[str]) -> dict[str, Any]:
    """权限码前后差集，给审计的 `detail` 用。

    与 `_snapshot_detail` 分开：那个给人读（写进 `app_meta_history.detail`），
    这个是给机器读的（审计表里按 `detail` 聚合「谁在批量加权限」）。
    审计的用途是追责与告警，`{"added": ["task:create"]}` 比一句中文更好用；
    而 `app_meta` 那条是运维翻历史时看的，中文更直接。两处都保留。
    """
    before, after = set(previous), set(target)
    return {
        "added": sorted(after - before),
        "removed": sorted(before - after),
        "countFrom": len(before),
        "countTo": len(after),
    }


def _clip(value: str, limit: int) -> str:
    """截断用户可控字符串再入库。

    `user_agent` / `ip` 都来自请求头，长度不设上限时，一条 1MB 的 UA 就能把
    审计表撑成体积问题；这类字段的用途是「大致认出是哪台设备」，截断无损。
    """
    return (value or "").strip()[:limit]


def _bearer_value(authorization: str | None) -> str:
    """从 `Authorization: Bearer xxx` 里取出令牌。

    这里没用 FastAPI 的 `HTTPBearer` 依赖：登出接口要在「令牌已经失效」时也能
    正常工作（幂等），而那个依赖会在解析阶段就把无效令牌打回 403/401。
    """
    if not authorization:
        return ""
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()


def _new_jti() -> str:
    """刷新令牌 id。用 `token_hex` 而不是 uuid：它只用于比对，不需要任何结构。"""
    return secrets.token_hex(16)


# 用户不存在时也要跑一次哈希校验，否则「响应快 = 用户名不存在」就是一个免费的账号枚举接口
_DUMMY_HASH: str | None = None


async def _dummy_hash() -> str:
    """惰性生成一个假口令哈希（首次登录时算一次，之后复用）。

    走线程池：这一步本身也是一次 scrypt，留在事件循环里同样会卡住全进程。
    并发首次调用可能各算一次，多算一次的成本远低于加锁的复杂度。
    """
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = await hash_password_async(secrets.token_hex(16))
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


def _permission_out(spec: PermissionSpec) -> PermissionOut:
    """权限目录的一项。页面项额外带上 `requires` 与 `route`。

    这两个字段是前端画「页面 → 接口」层级的唯一依据：目录里只有一句
    「关联接口：a, b, c」的话，界面就得靠字符串解析或者让人自己拼 ——
    两者都会让「层级关系」看不出来。
    """
    page = page_catalog.get(spec.code)
    return PermissionOut(
        code=spec.code,
        name=spec.name,
        description=spec.description,
        requires=list(page_catalog.requires_of(spec.code)) if page else [],
        route=page.route if page else "",
    )


def _user_fields(user: User, roles: Sequence[Role]) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "avatar_url": user.avatar_url,
        "phone": user.phone,
        "position": user.position,
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


def _record_failure(user_key: str, ip_key: str) -> None:
    """失败计数同时记到用户名桶与 IP 桶（任一到阈值即 429）。"""
    throttle.record_failure(user_key)
    if ip_key:
        throttle.record_failure(ip_key)


class IdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.roles = RoleRepository(session)
        self.sessions = SessionRepository(session)
        self.logins = LoginEventRepository(session)
        self.layers = PermissionLayerRepository(session)

    # ------------------------------------------------------------------ #
    # 认证 / 解析
    # ------------------------------------------------------------------ #
    async def authenticate(
        self, username: str, password: str, *, client_ip: str = "", user_agent: str = ""
    ) -> User:
        """校验口令并记录登录时间。

        `username` 已在 `LoginIn` 里归一成小写；这里再 strip 一次，
        防止内部调用（CLI、脚本）绕过 schema 直接传脏值。

        顺序是刻意的：**先付等待成本 → 停用则早退 → 验密 → 失败才计数**。
        - 等待在 `scrypt` 之前：爆破的时间成本是真实的，CPU 成本也被压低。
        - **停用的账号在验密之前就拒**（R10-04）：放在验密之后，403 与 401 的差异
          就成了一个免费的「口令正确性验证器」，而且它永远走不到 429。
        - 锁定**不拦正确密码**：否则任何人用 5 个乱码就能把一个已知用户名锁 15 分钟，
          真实用户反而登不进来（R5-01）。计数的意义是「猜错的代价」，不是「禁言」。
        - 计数放在 `verify_password` 之后、抛 `CredentialInvalid` 之前：
          失败路径与成功路径耗时一致，不会从时间维泄漏「账号是否存在」。
        - 验密走线程池（`verify_password_async`）：scrypt 是 150ms 级纯 CPU 工作，
          留在事件循环里会让整个进程停摆，而不只是「这一个登录变慢」（R6-02）。
        - **查库与验密都在 `password_slot()` 里面**：线程池只收窄了并发运算数，
          排队等运算的请求此时已经握着一条数据库连接，几十个并发登录就能占满连接池、
          拖慢所有要查库的接口；而登录是唯一匿名可达的入口（R7-01）。
          闸门放在查库之前，等待发生在还没拿连接的时候。
        - `throttle.penalty()` 的等待留在闸门**外面**：那是有意的慢，不是并发，
          包进闸门等于让爆破者用 5 秒等待占满名额，比不加闸门更糟。
        """
        # 两个桶各记一份，任一到阈值即拦（见 `throttle` 文件头「两个桶」）：
        # 只按 `用户名|IP` 一个键的话，换源 IP 打同一账号每次都是新桶，
        # 单账号爆破实际没有上限；一台机器换用户名喷则完全没有惩罚。
        user_key = f"u:{username}"
        ip_key = f"i:{client_ip}" if client_ip else ""
        wait = throttle.penalty(user_key)
        if ip_key:
            wait = max(wait, throttle.penalty(ip_key, limit=throttle.IP_MAX_FAILURES))
        if wait:
            await asyncio.sleep(wait)

        async with password_slot():
            user = await self.users.get_by_username(username)

            # 停用账号在**验密之前**就拒掉（R10-04）。之前这一步在验密之后，
            # 而且前面先 `throttle.reset(user_key)`，于是有两个后果：
            # 1) 403 与 401 的差异等于「口令猜对了」——对已知/疑似停用的账号
            #    （离职员工、被冻结的可疑账号）可以慢速验证口令，而这类账号
            #    恰恰是攻击者最想确认的；
            # 2) 对口令的路径每次都把计数清零，403 永远不会变成 429，
            #    等于可以无限次验证，成本只有攻击者自己的 CPU。
            # 代价是「账号存在且被停用」由 403 可见——这个信息本来就要给用户看
            # （登录页那句「账号已停用，请联系管理员」），不是泄漏。
            # 也正因为状态码已经说明了这件事，早退导致的计时差不再多泄漏什么。
            if user is not None and not user.is_active:
                _record_failure(user_key, ip_key)
                await self._audit_login(
                    user_id=user.id,
                    username=username,
                    success=False,
                    reason="disabled",
                    ip=client_ip,
                    user_agent=user_agent,
                    fold=throttle.AUDIT_FOLD_SEC,
                )
                raise AccountDisabled()

            encoded = user.password_hash if user is not None else await _dummy_hash()
            ok = await verify_password_async(password, encoded)
            if user is None or not ok:
                _record_failure(user_key, ip_key)
                locked = throttle.is_locked(user_key) or bool(
                    ip_key and throttle.is_locked(ip_key, limit=throttle.IP_MAX_FAILURES)
                )
                # 审计**在闸门里**写：这条路径原本是 `rollback()` 结束只读事务把连接
                # 还回池，现在换成「插一行 + commit」——同样是结束事务、同样只占一个
                # 连接，峰值不变（R7-01 / R9-01 的闸门容量仍然成立）。
                # 但审计必须落库：失败登录恰恰是最需要留痕的那一半，而异常路径上
                # `get_session` 会回滚整个事务，留给请求结束写就等于什么都没写。
                await self._audit_login(
                    user_id=user.id if user is not None else None,
                    username=username,
                    success=False,
                    # 已经被限流锁定：这一次失败的原因记成 rate_limited，
                    # 否则「同一个 IP 连错 50 次」在审计里看不出被拦过
                    reason="rate_limited" if locked else "bad_password",
                    ip=client_ip,
                    user_agent=user_agent,
                    # 已经进限流窗口的，每 15 分钟只留一条；普通的错口令 60 秒一条。
                    # 不折叠的话，被 429 拦下的请求照样 insert + commit，
                    # 限流对写入成本毫无作用（R10-06）。
                    fold=(
                        throttle.AUDIT_LOCK_FOLD_SEC
                        if locked
                        else throttle.AUDIT_FOLD_SEC
                    ),
                )
                # 已经猜错太多次：给 429，让前端提示「别再试了」，而不是无限 401
                throttle.reject(user_key)
                if ip_key:
                    throttle.reject(ip_key, limit=throttle.IP_MAX_FAILURES)
                raise CredentialInvalid()

            # 只清用户名桶：清 IP 桶等于给「拿一个已知正确的账号穿插着登」
            # 开一条把喷洒计数归零的通道，那一档就白设了。
            throttle.reset(user_key)

            user.last_login_at = utcnow()
            await self._audit_login(
                user_id=user.id,
                username=username,
                success=True,
                reason=None,
                ip=client_ip,
                user_agent=user_agent,
            )
            return user

    async def _audit_login(
        self,
        *,
        user_id: str | None,
        username: str,
        success: bool,
        reason: str | None,
        ip: str,
        user_agent: str,
        fold: float = 0.0,
    ) -> None:
        """写一条登录审计并当场提交。

        用户名不存在时 `user_id` 为 NULL，但 `username` 照样记——爆破脚本打的大多是
        不存在的账号，这条记录是唯一能看出「有人在扫」的东西。

        `fold > 0` 表示**同一 (原因, 用户名, IP) 在这个窗口内只写一条**（R10-06）。
        匿名方本来就能线性地往这张表里塞行：换用户名就换限流键，而
        `rate_limited` 那几次也照样 insert + commit——限流对写入成本毫无作用。
        折叠的取舍是明确的：审计的用途是「发现有人在试」，同一对来源的第一次
        就够说明问题，第 2~N 次没有新增信息。成功登录**不折叠**。

        提交失败只记日志、不影响登录：审计是旁路，不能让它的故障变成「全站登不进」。
        """
        if fold and not throttle.audit_once(
            f"{reason or '-'}|{username}|{ip}", fold
        ):
            return
        self.session.add(
            LoginEvent(
                user_id=user_id,
                username=_clip(username, 64),
                success=success,
                ip=_clip(ip, 64),
                user_agent=_clip(user_agent, 256),
                reason=reason,
            )
        )
        try:
            await self.session.commit()
        except SQLAlchemyError:  # pragma: no cover - 审计故障不阻断登录
            logger.exception("写登录审计失败：%s", username)
            await self.session.rollback()

    async def _audit_change(
        self,
        action: str,
        actor_id: str,
        *,
        target_type: str,
        target_id: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """记一条「授权面」的操作留痕。

        为什么身份模块必须有它：`reset_password` 是**最强提权面**（不需要对方配合，
        重置完直接以对方身份登录），`set_user_permissions` 是「谁能看见什么」的
        唯一开关。这两件事原先只写 `logger.info`，日志一滚动就无从追责 ——
        而 `audit_events` 表、`app audit` 查询命令、导出源早就都有了
        （见 `core/audit.py` 文件头），缺的只是这里没接线。

        调用顺序：**业务 commit 之后**。审计走独立会话（`db/audit_sink.py`），
        所以业务回滚了也照样留得下「他尝试过」；而 `record()` 自己吞掉异常，
        审计故障不会把一次成功的授权变成 500。

        `detail` 里不许出现口令、令牌、密码哈希 —— 审计表长期保留，写进去收不回来。
        """
        await audit.record(
            action,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            detail=detail or {},
        )

    async def resolve_identity(
        self, user_id: str, *, apply_page_gate: bool = True
    ) -> ResolvedIdentity | None:
        """用户 id → 权威身份（角色 + 权限）。账号不存在返回 None。

        权限来自**四层**：团队基础 ∪ 所属小组 ∪ 个人 ∪ 角色，再过两道减法 ——
        本人例外（`deny`）与页面门控。整条链只有一个实现：`core.authz.evaluate`，
        这里不再自己拼集合，否则「接口 403 但总览说他有权」这种自相矛盾迟早出现。

        两道减法的顺序有讲究（见 `core.authz` 的模块文档）：
        摊开通配 → 减个人例外 → 页面门控。

        `apply_page_gate=False` 只跳过**门控**，**不跳过例外**：
        这个分支回答的是「他拥有什么、能不能授给别人」（`_actor_codes`）。
        被自己否决掉的码不该算拥有 —— 否则他能把已经放弃的权限授出去。

        任一层出现 `*` 都等价于超管：只认角色上的 `is_superuser` 会让
        「给某人单独授 `*`」这条合法操作失效。例外对这种账号不生效，
        所以写入口直接拒（见 `set_permission_exceptions`）。
        """
        user = await self.users.get(user_id)
        if user is None:
            return None

        roles = tuple((await self.users.roles_for([user.id])).get(user.id, ()))
        if any(role.is_superuser for role in roles):
            # 超管直接给 `*`：新模块上线、新权限点声明后自动生效，不需要逐条授权。
            # 短路掉三层查询：超管的权限集已经是全集，再查也没有信息量。
            return ResolvedIdentity(user, roles, frozenset({SUPER_PERMISSION}))

        perms = await self.roles.permissions_for([role.id for role in roles])
        codes: set[str] = set()
        for role in roles:
            codes.update(perms.get(role.id, ()))
        layers = await self.layers.layer_codes(user.id)
        codes.update(layers.all_codes())
        if not apply_page_gate:
            # 「能不能授权」看原始集合（不门控），但仍要摊开通配再减例外 ——
            # 不摊的话，角色里的 `task:*` 会把你刚否决的 `task:delete` 盖回来，
            # 于是「被否决的码还能授给别人」= 提权绕过。
            return ResolvedIdentity(
                user,
                roles,
                frozenset(expand_grants(codes) - set(layers.denies)),
            )
        return ResolvedIdentity(user, roles, evaluate(codes, layers.denies).effective)

    # ------------------------------------------------------------------ #
    # 会话与令牌
    # ------------------------------------------------------------------ #
    async def build_token(
        self,
        user: User,
        identity: ResolvedIdentity,
        *,
        user_agent: str = "",
        ip: str = "",
    ) -> TokenOut:
        """登录成功：落一条会话，签一对令牌。

        为什么登录就要落一行会话：不落这一行，后面「登出 / 踢下线 / 我在哪些设备上
        登录过」都没有主语。`sid` 写进 access 令牌，解析器每请求比对一次——
        令牌是无状态的，只靠签名和 TTL 的话，登出就永远只是「前端删了本地副本」。
        """
        session = UserSession(
            user_id=user.id,
            refresh_jti=_new_jti(),
            user_agent=_clip(user_agent, 256),
            ip=_clip(ip, 64),
            last_seen_at=utcnow(),
            expires_at=utcnow() + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
        self.session.add(session)
        await self.session.flush()
        # 落库再签发：会话没写进去的话，签出来的令牌第一次用就会被解析器拒掉
        # （`sid` 查不到会话），而登录接口本身是 200——这种「登录成功但立刻 401」
        # 极难从错误信息里看出来。
        await self.session.commit()
        return self._issue(identity, session)

    def _issue(self, identity: ResolvedIdentity, session: UserSession) -> TokenOut:
        user = identity.user
        access = create_access_token(
            user.id,
            name=user.display_name or user.username,
            roles=list(identity.role_codes),
            # 令牌里的 perms 只作调试参考：权威来源是请求期的 resolve_principal
            permissions=sorted(identity.permissions),
            session_id=session.id,
        )
        return TokenOut(
            access_token=access,
            token_type="Bearer",
            expires_in=settings.access_token_ttl_seconds,
            refresh_token=create_refresh_token(
                user.id, session_id=session.id, jti=session.refresh_jti
            ),
            refresh_expires_in=settings.refresh_token_ttl_seconds,
            user=self._to_me(identity),
        )

    async def refresh(
        self, refresh_token: str, *, user_agent: str = "", ip: str = ""
    ) -> TokenOut:
        """用刷新令牌换一对新令牌（**轮换**：旧 refresh 立即作废）。

        判定顺序是刻意的，每一步对应一种「票不能再用」的具体原因：
        签名/过期 → 会话还在吗 → 这张 jti 是不是当前那张 → 账号还好吗 → 改过密吗。

        关于「重放检测」：jti 对不上时只拒绝、**不吊销整条会话**。理由是两个标签页
        同时续期时，后到的那个必然拿着旧 jti——把它当攻击处理，等于让用户因为
        「开了两个标签页」被迫重新登录。被偷走的旧 refresh 本来也已经换不出东西了。
        """
        try:
            claims = decode_refresh_token(refresh_token)
        except Unauthorized as exc:
            # 统一成 40122：前端只需要处理「刷新失败 → 回登录页」这一种结局，
            # 不需要区分「签名坏了」和「过期了」
            raise RefreshTokenInvalid() from exc

        session_id = str(claims["sid"])
        jti = str(claims["jti"])
        user_id = str(claims["sub"])

        session = await self.sessions.active_by_id(session_id, user_id)
        if session is None or session.refresh_jti != jti:
            if session is not None:
                # 会话还在、jti 却是旧的：这张票已经被轮换过了。
                #
                # 两种来源在请求本身里**无法区分**：两个标签页抢跑（常见，正常），
                # 或者票被复制出去、两边各换一次（严重）。所以处置保持原样——
                # 只拒绝、不吊销会话，否则开两个标签页的用户会被迫重新登录；
                # 但必须留下痕迹：真被盗时，这里是唯一能看出「同一条会话被两处
                # 轮换」的地方。`app audit --action session.refresh_reuse` 一查即知。
                await audit.record(
                    "session.refresh_reuse",
                    actor_id=user_id,
                    target_type="session",
                    target_id=session_id,
                    detail={
                        "ip": _clip(ip, 64),
                        "userAgent": _clip(user_agent, 256),
                    },
                )
            raise RefreshTokenInvalid()

        identity = await self.resolve_identity(user_id)
        if identity is None or not identity.user.is_active:
            raise RefreshTokenInvalid()
        # 与 access 令牌同一套判定：改密之前签发的票一律作废。
        # 不做这一步的话，攻击者手里那张 refresh 能在「改密自救」之后继续换新令牌，
        # 前面那套 `password_changed_at` 就只防住了一半。
        issued_ms = int(claims.get("iat_ms") or 0) or int(claims.get("iat") or 0) * 1000
        if (
            issued_ms
            and identity.user.password_changed_at is not None
            and issued_ms < int(identity.user.password_changed_at.timestamp() * 1000)
        ):
            raise RefreshTokenInvalid()

        # 轮换是**抢占**，不是「读-比较-写」：条件更新要求 `refresh_jti` 仍然
        # 等于我带来的这一张。两个并发请求拿着同一张 refresh 令牌时，「读-比较-
        # 写」会让两个都通过检查、各写各的新 jti —— 同一张票换来两对有效令牌，
        # 而重放检测正是在这条路上失效。条件更新只有一个能换掉 jti。
        now = utcnow()
        values: dict[str, Any] = {
            "refresh_jti": _new_jti(),
            "last_seen_at": now,
            # 滑动延长：活跃用户不该在第 7 天被强制下线；长期不用的会话则按时过期
            "expires_at": now + timedelta(seconds=settings.refresh_token_ttl_seconds),
        }
        if user_agent:
            values["user_agent"] = _clip(user_agent, 256)
        if ip:
            values["ip"] = _clip(ip, 64)
        claimed = cast(
            "CursorResult[Any]",
            await self.session.execute(
                update(UserSession)
                .where(
                    UserSession.id == session_id,
                    UserSession.refresh_jti == jti,
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            ),
        )
        if claimed.rowcount != 1:
            # 已经被另一个并发请求换掉了：这张票已经用过了
            await self.session.rollback()
            raise RefreshTokenInvalid()
        await self.session.commit()
        await self.session.refresh(session)
        return self._issue(identity, session)

    async def logout_by_token(self, authorization: str | None) -> None:
        """登出：作废当前会话。**幂等**——令牌过期、已吊销、格式不对都算成功。

        为什么不等 `get_actor` 校验完再吊销：那样「已经登出过的令牌」会在解析阶段
        401，用户第二次点退出就会看到一条报错。退出登录的结果是确定的
        （你现在没有有效会话），过程不重要。
        """
        token = _bearer_value(authorization)
        if not token:
            return
        try:
            claims = decode_access_token(token)
        except Unauthorized:
            return
        session_id = str(claims.get("sid") or "")
        if not session_id:  # pragma: no cover - 无 sid 的令牌本来就没有会话可吊销
            return
        revoked = await self.sessions.revoke(
            session_id, reason="logout", user_id=str(claims.get("sub") or "")
        )
        if revoked:
            await self.session.commit()

    async def list_sessions(self, actor: CurrentUser) -> list[SessionOut]:
        """当前账号的有效会话。`current` 靠令牌里的 sid 比对，前端据此标「本机」。"""
        rows = await self.sessions.list_for_user(actor.id, limit=SESSION_LIST_LIMIT)
        return [
            SessionOut(
                id=row.id,
                user_agent=row.user_agent,
                ip=row.ip,
                last_seen_at=row.last_seen_at,
                expires_at=row.expires_at,
                created_at=row.created_at,
                current=row.id == actor.session_id,
            )
            for row in rows
        ]

    async def revoke_session(self, actor: CurrentUser, session_id: str) -> None:
        """踢掉自己的一条会话。**只能踢自己的**：别人的 sid 在这里查不到，直接 404。"""
        if not await self.sessions.revoke(
            session_id, reason="self_revoke", user_id=actor.id
        ):
            raise SessionNotFound()
        await self.session.commit()

    async def revoke_other_sessions(self, actor: CurrentUser) -> int:
        """退出所有其他设备，保留当前会话。

        当前令牌没有 `sid`（旧令牌 / 合成主体）时无从判断「哪条是当前」，
        此时全部吊销——这类令牌本来也无法被单点吊销，留着反而是个例外。
        """
        count = await self.sessions.revoke_all(
            actor.id, reason="self_revoke", keep_session_id=actor.session_id or None
        )
        await self.session.commit()
        return count

    async def revoke_user_sessions(self, user_id: str, actor: CurrentUser) -> int:
        """管理员踢某人下线（`POST /users/{id}/revoke-sessions`）。

        守卫与重置口令同级：踢下线是**拿走对方的访问权**，目标不能高于自己。
        """
        user = await self._require_user(user_id)
        await self._guard_target_not_above(user, await self._actor_codes(actor), actor)
        count = await self.sessions.revoke_all(user.id, reason="admin_revoke")
        await self.session.commit()
        logger.info("管理员 %s 吊销了 %s 的 %d 个会话", actor.id, user.username, count)
        return count

    # ------------------------------------------------------------------ #
    # 登录审计
    # ------------------------------------------------------------------ #
    async def my_logins(self, actor: CurrentUser, limit: int) -> LoginEventListOut:
        return await self._login_events(actor.id, limit)

    async def logins_of(
        self, user_id: str, limit: int, actor: CurrentUser
    ) -> LoginEventListOut:
        """管理员查某账号的登录记录。

        读也要过层级守卫（R10-05）：返回的是 IP + UA + 失败原因，够推断一个人的
        常用网络、设备与作息，而「凌晨三点有一次失败登录」本身就是安全事件。
        `identity:read` 的语义是「能看用户管理页面」，那个页面里没有 IP——
        让它顺带能读超管审计，等于把「读」这半边漏在守卫之外。
        """
        user = await self._require_user(user_id)  # 404 而不是空列表：id 写错要能看出来
        await self._guard_audit_target(user, actor)
        return await self._login_events(user.id, limit)

    async def _login_events(self, user_id: str, limit: int) -> LoginEventListOut:
        rows = await self.logins.latest_for_user(user_id, limit)
        return LoginEventListOut(
            items=[
                LoginEventOut(
                    at=row.created_at,
                    success=row.success,
                    ip=row.ip,
                    user_agent=row.user_agent,
                    reason=row.reason,
                )
                for row in rows
            ]
        )

    @staticmethod
    def password_policy() -> PasswordPolicyOut:
        """口令策略快照。匿名可访问：登录页和「管理员重置」表单都要在未登录时用它。"""
        return PasswordPolicyOut(**policy.describe())

    async def purge_login_events(self, days: int) -> int:
        """清理 `days` 天前的审计。由 `app prune-logins` 调用，不在请求路径上跑。"""
        cutoff = utcnow() - timedelta(days=max(days, 1))
        removed = await self.logins.purge_before(cutoff)
        await self.session.commit()
        return removed

    async def purge_sessions(self, days: int) -> int:
        """清理 `days` 天前就已经过期的会话行（`app prune-sessions`）。

        `expires_at` 一过，这一行就再也不会被读到，留着的唯一作用是撑大表。
        保留期不需要比 refresh TTL 长，「谁在哪台设备登录过」由 `login_events` 负责，
        会话表只管「现在还允许谁用哪张票」。
        """
        cutoff = utcnow() - timedelta(days=max(days, 1))
        removed = await self.sessions.purge_expired(cutoff)
        await self.session.commit()
        return removed

    @staticmethod
    def _to_me(identity: ResolvedIdentity) -> MeOut:
        return MeOut(
            **_user_fields(identity.user, identity.roles),
            permissions=sorted(identity.permissions),
        )

    async def me(self, actor: CurrentUser) -> MeOut:
        """当前用户资料。

        权限直接用请求期已经解析好的 `actor.permissions`，**不再重新展开角色权限**：
        `get_actor` 对每条带令牌的请求都查过「用户 + 角色 + 角色权限」，
        这里再走一次 `resolve_identity` 就是纯翻倍的查询（R5-07）。
        用户行与角色摘要仍要读——`MeOut` 要的 `username` / `email` / `isActive`
        与角色 id 属于「资料」，不在鉴权用的最小投影 `CurrentUser` 里。
        """
        user = await self._require_user(actor.id)
        roles = (await self.users.roles_for([user.id])).get(user.id, [])
        return MeOut(**_user_fields(user, roles), permissions=sorted(actor.permissions))

    # ------------------------------------------------------------------ #
    # 自助
    # ------------------------------------------------------------------ #
    async def update_me(self, actor: CurrentUser, payload: ProfileUpdateIn) -> MeOut:
        """自助改资料：**只允许改展示字段**。

        这里刻意不接 `UserUpdateIn`（那是管理员用的，含 `is_active`）：
        自助路径若能把 `is_active` 改成 false，用户点错一次就把自己锁在门外，
        而恢复只有管理员一个入口。字段白名单比 `setattr` 循环更难写错——
        将来给 `UserUpdateIn` 加字段（比如 `role_ids`）也不会自动漏进自助口子。
        """
        user = await self._require_user(actor.id)
        fields = payload.model_dump(exclude_unset=True)
        for field in _PROFILE_FIELDS:
            if field in fields:
                setattr(user, field, fields[field])
        await self.session.commit()
        return await self.me(actor)

    async def change_password(self, user_id: str, payload: PasswordChangeIn) -> None:
        """自助改密：这条路径要算**两次** scrypt（验旧 + 算新）。

        闸门必须在 `_require_user` **之前**。放在后面就是 R7-01 的同一个坑挪了个门：
        旧口令传错时只花一次哈希、不改任何状态，可以无限重放，而这条路由只需要登录、
        不需要任何权限点——一个只读账号 20 个并发就能把连接池顶满（R8-01）。

        失败计数键用 `user_id` 而不是 IP：这里已经是登录态，IP 只会在同一个办公室
        的 NAT 后面把所有人算成一个；而这条路径要防的是「拿到令牌后猜口令」。
        """
        key = f"pw:{user_id}"
        async with password_slot():
            user = await self._require_user(user_id)
            ok = await verify_password_async(payload.old_password, user.password_hash)
            if not ok:
                await self.session.rollback()  # 失败路径不需要事务，先把连接还回池
                throttle.record_failure(key)
                throttle.reject(key)
                raise OldPasswordMismatch()
            throttle.reset(key)
            await self.set_password(user, payload.new_password)
            # 改密必须同时吊销会话（R10-03）。`password_changed_at` 已经让旧票失效，
            # 但会话表里的行没有任何标记，而 `list_for_user` 的「有效」定义是
            # `revoked_at IS NULL AND expires_at > now`——僵尸会话全部满足，
            # 「我的登录设备」于是把几台已经换不出票的设备列成在线，要等 7 天
            # 自然过期才消失。这正是 `reset_password` 早就写过的那句话。
            #
            # 当前这台也在内：它的 refresh 同样过不了 `iat < password_changed_at`
            # （续期判断与解析器同一套），把它留着只是让列表继续说谎。
            # 所以改密后的确定状态是「全部下线、回登录页」，前端不必猜。
            await self.sessions.revoke_all(user.id, reason="password_change")
            await self.session.commit()
            # 留痕必须在**闸门内、commit 之后**：commit 已经把本请求的连接还回池，
            # 审计再借一条，每个名额的峰值仍然是 1 条。放到闸门外就变成
            # 「40 个并发一起借连接」，正是 R8-01 要挡的那个形状
            # （`test_login_gate` 会把它抓成连接峰值超标）。
            await self._audit_change(
                "identity.user.password_change",
                user_id,
                target_type="user",
                target_id=user_id,
                detail={"source": "self"},
            )

    # ------------------------------------------------------------------ #
    # 用户管理
    # ------------------------------------------------------------------ #
    async def list_users(
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
    ) -> Page[UserOut]:
        rows, total = await self.users.page(
            params,
            keyword=keyword,
            name=name,
            username=username,
            phone=phone,
            is_active=is_active,
            role_id=role_id,
            group_id=group_id,
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
        # 整段在名额里：`set_password` 要算一次 scrypt，而这条路径从第一句起就要查库。
        # 先排队、再拿连接，才不会出现「几十个建号请求各自握着一条连接等哈希」。
        async with password_slot():
            if await self.users.get_by_username(payload.username) is not None:
                raise UsernameTaken()

            roles = await self._roles_by_ids(payload.role_ids)
            # 建号时直接挂超管角色 = 造一个自己登进去就是超管的账号，
            # 必须和 set_user_roles 走同一道闸（R4-11 路径 C）
            await self._guard_role_grant(roles, await self._actor_codes(actor), actor)

            # 建号顺带入组：一个请求一个事务，不搞「先建号再调 set_members」——
            # 那会在第二步失败时留下一个没进组的半成品账号。
            group_ids = list(dict.fromkeys(payload.group_ids))
            groups = await self.layers.groups_by_ids(group_ids)
            unknown = [gid for gid in group_ids if gid not in groups]
            if unknown:
                raise GroupNotFound(details={"unknown": unknown})
            # 入组会让新账号立刻继承该组权限码，和挂角色是同一条红线：
            # 不能借「给新人加组」授出操作者自己都没有的权限。
            # 组自身的权限是别处设的（set_group_permissions 已有同一道闸），
            # 这里只判断「这个组的操作者能不能碰」。
            for group in groups.values():
                self._guard_permission_grant(
                    await self.layers.grants(SCOPE_GROUP, group.id),
                    await self._actor_codes(actor),
                    what=f"小组 {group.name}",
                )

            user = User(
                username=payload.username,
                display_name=payload.display_name or payload.username,
                email=payload.email,
                avatar_url=payload.avatar_url,
                phone=payload.phone,
                position=payload.position,
                is_active=True,
            )
            # 建号也要写 `password_changed_at`：不走同一个方法就会出现「这个账号
            # 的口令没有建立时间」的第四种状态，而令牌失效判定依赖它
            await self.set_password(user, payload.password)
            self.session.add(user)
            await self.session.flush()
            await self.users.set_roles(user.id, [role.id for role in roles])
            await self.layers.add_to_groups(user.id, group_ids)
            await self.session.commit()
            # 与 `change_password` 同一条理由：闸门内、commit 之后（连接已归还）
            await self._audit_change(
                "identity.user.create",
                actor.id,
                target_type="user",
                target_id=user.id,
                detail={
                    "username": user.username,
                    "roles": sorted(role.code for role in roles),
                    "groups": sorted(group_ids),
                },
            )
        logger.info("创建用户 %s（%s），加入 %d 个小组", user.username, user.id, len(group_ids))
        return UserOut(**_user_fields(user, roles))

    async def update_user(
        self, user_id: str, payload: UserUpdateIn, actor: CurrentUser
    ) -> UserOut:
        user = await self._require_user(user_id)
        fields = payload.model_dump(exclude_unset=True)

        # 守卫按「**要写什么**」判定，不按「哪个字段」「值变没变」判定。
        # 两次都栽在这上面：R7-03 只守了 email，同一次请求里改昵称、换头像照旧 200（R8-02）；
        # 而「值没变就免检」只是把同一个洞挪到另一个输入上（R8-04）。
        # 这里一次判完，但**按字段的风险等级分两档**（R9-02）：
        # - 写 `is_active` / 邮箱 → 等同于拿走或接管对方的访问权，用严格守卫；
        # - 只写展示字段 → 防冒充即可，目标不是超管就放行，
        #   否则「能改昵称」就等于要求「操作者持有被管理者的一切业务权限」。
        # 两个都写时走严格的那档——严格守卫覆盖的范围更大，不需要再跑一遍轻的。
        # 改自己的不经过这里：自助路径是 `update_me`，只接受展示字段白名单。
        writing_display = any(field in fields for field in _DISPLAY_FIELDS)
        writing_recovery = any(field in fields for field in _RECOVERY_FIELDS)
        writing_active = fields.get("is_active") is not None
        if user.id != actor.id:
            if writing_recovery or writing_active:
                await self._guard_target_not_above(
                    user, await self._actor_codes(actor), actor
                )
            elif writing_display:
                await self._guard_display_target(user, actor)

        # 停用 / 启用是**同一个能力的两端**，而这条路由只需要 `identity:update`。
        # 少了这道闸，「可以编辑资料、但不能停用」这种角色根本表达不出来 ——
        # 两个权限点（update / delete）承诺的是同一件事，最小组件原则失效。
        # 放在「目标不高于自己」之后：越级时要先报越级（40323），
        # 而不是先报「你少了 identity:delete」，那会让人去改错的开关。
        if writing_active and not actor.has(PERM_DELETE):
            raise AccessSwitchForbidden()

        # `is_active` 的三种取值必须分开：**没传**（不动）/ true / false。
        # 用 `in fields` 会让「传了 null」走进 bool(None) = False（R6-03），
        # schema 已经挡了一次 422，这里再挡一次是为了内部调用（脚本、CLI）绕不过去。
        # 停用与恢复**两个方向**都要过守卫（R6-04）：安全事件后「先停用、再启用」，
        # 如果启用不需要超管，一个中级管理员就能把刚被停用的内鬼账号放回来。
        revoked = 0
        if writing_active:
            active = bool(fields["is_active"])
            if active is False and user.id == actor.id:
                raise CannotTargetSelf()
            if active:
                user.is_active = True
            else:
                # 停用**只能**走 `_cut_access`：置 `is_active` 与吊销会话是同一件事，
                # 两条停用路径（DELETE / PATCH）各写一半，就又回到 R10-02。
                revoked = await self._cut_access(user, reason="deactivate")

        for field in _PROFILE_FIELDS:
            if field in fields:
                setattr(user, field, fields[field])

        await self.session.flush()
        if fields.get("is_active") is False:
            await self._guard_has_superuser()
        await self.session.commit()
        if writing_active:
            await self._audit_change(
                "identity.user.activate"
                if fields["is_active"]
                else "identity.user.deactivate",
                actor.id,
                target_type="user",
                target_id=user.id,
                detail={"revokedSessions": revoked, "via": "patch"},
            )
        return await self.get_user(user.id)

    async def set_user_roles(
        self, user_id: str, role_ids: Sequence[str], actor: CurrentUser
    ) -> UserOut:
        user = await self._require_user(user_id)
        # 撤权方向与授予方向必须对等：改别人的角色集合，等于改别人的权限，
        # 而「不能操作权限高于自己的账号」这条红线在改密 / 停用上已经写了，
        # 这里漏掉就出现「给不了、但能撤」——role:manage 一个人能把 editor 清空（R5-03）。
        # 放在授予检查之前：目标高于自己时先报「越级」，而不是先报「子集不满足」。
        await self._guard_target_not_above(user, await self._actor_codes(actor), actor)
        roles = await self._roles_by_ids(role_ids)

        current = (await self.users.roles_for([user.id])).get(user.id, [])
        touches_superuser = any(role.is_superuser for role in roles) or any(
            role.is_superuser for role in current
        )
        # 授超管和撤超管都是提权面：只有超管能做，否则「能改角色」等于「能给自己发超管」
        if touches_superuser and not actor.has(SUPER_PERMISSION):
            raise SuperuserRequired()
        # 非超管角色同样要挡：给自己挂一个含 `task:*` 的角色，等于绕过「只读」授权
        await self._guard_role_grant(roles, await self._actor_codes(actor), actor)

        await self.users.set_roles(user.id, [role.id for role in roles])
        await self._guard_has_superuser()
        await self.session.commit()

        await self._audit_change(
            "identity.user.roles",
            actor.id,
            target_type="user",
            target_id=user.id,
            detail={
                "from": sorted(role.code for role in current),
                "to": sorted(role.code for role in roles),
            },
        )
        logger.info("调整用户角色 %s -> %s", user.username, [role.code for role in roles])
        return UserOut(**_user_fields(user, roles))

    async def reset_password(
        self, user_id: str, new_password: str, actor: CurrentUser
    ) -> None:
        """管理员重置口令。

        这是**最强的提权面**：不需要对方配合，重置完直接以对方身份登录。
        所以目标账号的权限必须不高于操作者——否则 `identity:update` 就等于 `*`
        （R4-11 路径 B）。

        闸门必须在 `_require_user` **之前**：先把名额拿到再借连接。放在守卫之后就
        成了「先借连接、再排队」，20 并发能把连接池顶满（R9-01）——同一份文件里的
        `create_user` 就是正确写法，两处不该相反。守卫在闸门内跑只多占一个名额
        （`resolve_identity` 是几条短查询，不涉及 scrypt），峰值仍是闸门容量。
        """
        async with password_slot():
            user = await self._require_user(user_id)
            await self._guard_target_not_above(
                user, await self._actor_codes(actor), actor
            )
            await self.set_password(user, new_password)
            # 顺手吊销目标的所有会话：改密本身已经让旧 access 失效
            # （`password_changed_at`），但「会话列表里还挂着几台设备」会让管理员
            # 以为没踢干净；而这张表存在的意义就是「谁的会话还在」要看得见。
            await self.sessions.revoke_all(user.id, reason="password_reset")
            await self.session.commit()
            # 闸门内、commit 之后：连接已归还，审计再借一条不抬高峰值
            await self._audit_change(
                "identity.user.password_reset",
                actor.id,
                target_type="user",
                target_id=user.id,
                detail={"username": user.username},
            )
        logger.info("管理员 %s 重置了 %s 的密码", actor.id, user.username)

    async def deactivate_user(self, user_id: str, actor: CurrentUser) -> None:
        """`DELETE /users/{id}` 的真实语义：停用，不做物理删除。

        历史任务的 `author_id` 还指着他，删掉行会让「谁做的」变成悬空引用。

        停用 = 置 `is_active` **且**吊销全部会话（`_cut_access`）。少了后半句，
        停用就只是「暂停」：停用期间令牌被 `is_active` 拦住，但会话行没被标记，
        管理员一启用，同一张 access 与 refresh 原地复活（R10-02）。
        """
        user = await self._require_user(user_id)
        if user.id == actor.id:
            raise CannotTargetSelf()
        await self._guard_target_not_above(user, await self._actor_codes(actor), actor)
        revoked = await self._cut_access(user, reason="deactivate")
        await self._guard_has_superuser()
        await self.session.commit()
        await self._audit_change(
            "identity.user.deactivate",
            actor.id,
            target_type="user",
            target_id=user.id,
            detail={"username": user.username, "revokedSessions": revoked, "via": "delete"},
        )
        logger.info("停用用户 %s，吊销 %d 个会话", user.username, revoked)

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
        self._guard_permission_grant(
            payload.permissions, await self._actor_codes(actor), what=f"角色 {code}"
        )
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
        await self._audit_change(
            "identity.role.create",
            actor.id,
            target_type="role",
            target_id=role.id,
            detail={
                "code": code,
                "isSuperuser": payload.is_superuser,
                "permissions": sorted(payload.permissions),
            },
        )
        return await self.get_role(role.id)

    async def update_role(
        self, role_id: str, payload: RoleUpdateIn, actor: CurrentUser
    ) -> RoleOut:
        role = await self._require_role(role_id)
        fields = payload.model_dump(exclude_unset=True)
        before = {
            "name": role.name,
            "description": role.description,
            "isSuperuser": role.is_superuser,
        }

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
        await self._audit_change(
            "identity.role.update",
            actor.id,
            target_type="role",
            target_id=role.id,
            detail={
                "from": before,
                "to": {
                    "name": role.name,
                    "description": role.description,
                    "isSuperuser": role.is_superuser,
                },
            },
        )
        return await self.get_role(role.id)

    async def set_role_permissions(
        self, role_id: str, permissions: Sequence[str], actor: CurrentUser
    ) -> RoleOut:
        role = await self._require_role(role_id)
        self._validate_permissions(permissions)
        # 与 set_user_roles 同一条红线：比自己强的角色不能被"顺手改成只读"，
        # 那和把人降级是一回事，只是换了个入口（R5-03 的同族）。
        actor_codes = await self._actor_codes(actor)
        await self._guard_role_not_above(role, actor_codes, actor)
        # 超管角色的权限恒为 `*`：写进去也不会被判定读到（`resolve_identity` 短路），
        # 只会留在库里让界面显示一份假的配置。
        if role.is_superuser:
            raise SuperuserGrantForbidden(details={"role": role.code})
        self._guard_permission_grant(
            permissions, actor_codes, what=f"角色 {role.code}"
        )
        previous = list((await self.roles.permissions_for([role.id])).get(role.id, ()))
        await self.roles.set_permissions(role.id, permissions)
        await self.session.commit()
        await self._audit_change(
            "identity.role.permissions",
            actor.id,
            target_type="role",
            target_id=role.id,
            detail={"code": role.code, **_grant_diff(previous, permissions)},
        )
        return await self.get_role(role.id)

    async def delete_role(self, role_id: str, actor: CurrentUser) -> None:
        role = await self._require_role(role_id)
        if role.is_builtin:
            raise BuiltinRoleLocked()
        # 删掉一个比自己强的角色 = 让所有持有它的人瞬间掉权限，
        # 而角色本身没有 owner 字段可以判断"归谁管"（R5-06）
        await self._guard_role_not_above(role, await self._actor_codes(actor), actor)
        if (await self.roles.user_counts([role.id])).get(role.id, 0) > 0:
            raise RoleInUse()

        # 删完再读一次就会 `ObjectDeletedError`（行没了），所以先记下来
        code = role.code
        previous = list((await self.roles.permissions_for([role.id])).get(role.id, ()))
        await self.roles.drop_bindings(role.id)
        await self.session.delete(role)
        await self.session.flush()
        await self._guard_has_superuser()
        await self.session.commit()
        await self._audit_change(
            "identity.role.delete",
            actor.id,
            target_type="role",
            target_id=role_id,
            detail={"code": code, "permissions": previous},
        )

    # ------------------------------------------------------------------ #
    # 三层授权：团队 / 小组 / 个人
    # ------------------------------------------------------------------ #
    async def _require_group(self, group_id: str) -> Group:
        group = await self.layers.get_group(group_id)
        if group is None:
            raise GroupNotFound()
        return group

    async def _group_out(self, group: Group) -> GroupOut:
        members = (await self.layers.members_for([group.id])).get(group.id, [])
        return GroupOut(
            id=group.id,
            name=group.name,
            description=group.description,
            created_at=group.created_at,
            permissions=await self.layers.grants(SCOPE_GROUP, group.id),
            member_ids=sorted(members),
            member_count=len(members),
        )

    def _validate_team_grants(self, codes: Sequence[str]) -> None:
        """团队层 = 所有人：管理类权限、页面权限和 `*` 一律不许放进来。

        `*` 会把全站变成超管；`identity:*` / `identity:group:*` 等于给所有人
        发「管理组织」的票。校验放在服务层而不是只靠前端隐藏选项：
        直接打接口也要挡得住。

        `page:*` 与 `identity:*` 同一类，但理由不同：团队层没有 deny，
        页面放进来就再也拿不掉，而「关掉某个人进某一页」正是页面权限存在的意义。
        """
        self._validate_permissions(codes)
        forbidden = sorted(
            {
                code
                for code in codes
                if code == SUPER_PERMISSION
                or code.startswith(TEAM_FORBIDDEN_PREFIXES)
            }
        )
        if forbidden:
            raise TeamGrantForbidden(details={"forbidden": forbidden})

    def _guard_layer_not_above(
        self, codes: Sequence[str], actor_codes: set[str], *, what: str
    ) -> None:
        """撤权方向也要挡：清空一层比自己高的权限，和「把人降级」是同一件事。"""
        missing = self._missing_grants(actor_codes, codes)
        if missing:
            raise PermissionEscalation(
                f"不能改动权限高于自己的{what}", details={"missing": missing}
            )

    async def list_groups(self) -> list[GroupOut]:
        return [await self._group_out(group) for group in await self.layers.list_groups()]

    async def get_group(self, group_id: str) -> GroupOut:
        return await self._group_out(await self._require_group(group_id))

    async def create_group(
        self, payload: GroupCreateIn, actor: CurrentUser
    ) -> GroupOut:
        name = payload.name.strip()
        if await self.layers.get_group_by_name(name) is not None:
            raise GroupNameTaken()
        group = await self.layers.create_group(name, payload.description)
        await self.session.commit()
        await self._audit_change(
            "identity.group.create",
            actor.id,
            target_type="group",
            target_id=group.id,
            detail={"name": name},
        )
        logger.info("管理员 %s 新建小组 %s（%s）", actor.id, name, group.id)
        return await self._group_out(group)

    async def update_group(
        self, group_id: str, payload: GroupUpdateIn, actor: CurrentUser
    ) -> GroupOut:
        group = await self._require_group(group_id)
        fields = payload.model_dump(exclude_unset=True)
        if "name" in fields and fields["name"] is not None:
            name = fields["name"].strip()
            existing = await self.layers.get_group_by_name(name)
            if existing is not None and existing.id != group.id:
                raise GroupNameTaken()
            group.name = name
        if "description" in fields and fields["description"] is not None:
            group.description = fields["description"]
        await self.session.flush()
        await self.session.commit()
        logger.info("管理员 %s 编辑小组 %s", actor.id, group.name)
        return await self._group_out(group)

    async def delete_group(self, group_id: str, actor: CurrentUser) -> None:
        group = await self._require_group(group_id)
        current = await self.layers.grants(SCOPE_GROUP, group.id)
        self._guard_layer_not_above(current, await self._actor_codes(actor), what="小组")
        # 成员与 grant 由 repository 在同一个事务里清掉；没有外键级联可依赖
        await self.layers.delete_group(group.id)
        await self.session.commit()
        await self._audit_change(
            "identity.group.delete",
            actor.id,
            target_type="group",
            target_id=group_id,
            detail={"name": group.name, "permissions": current},
        )
        logger.info("管理员 %s 删除小组 %s", actor.id, group.name)

    async def group_members(self, group_id: str) -> list[UserOut]:
        """成员列表给「编辑小组成员」弹窗用：一次拿全，不按 id 逐个查。"""
        group = await self._require_group(group_id)
        ids = (await self.layers.members_for([group.id])).get(group.id, [])
        users = sorted(await self.users.get_many(ids), key=lambda u: u.username)
        roles_map = await self.users.roles_for([user.id for user in users])
        return [
            UserOut(**_user_fields(user, roles_map.get(user.id, ())))
            for user in users
        ]

    async def set_group_members(
        self, group_id: str, user_ids: Sequence[str], actor: CurrentUser
    ) -> GroupOut:
        group = await self._require_group(group_id)
        # 入组 = 立刻继承该组全部授权码（`resolve_identity` 会把组授权并进有效权限），
        # 所以「能排班」和「能授权」在这条路径上是同一件事。组自身的权限必须是
        # 操作者的子集，否则持有 `identity:group:member:manage` 的人可以把自己塞进
        # 高权组、把同伙拉进去、或把人踢出去（撤权），等于绕过
        # `identity:group:permission:manage`。与 `create_user` 的入组守卫、
        # `delete_group` / `set_group_permissions` 同口径。
        self._guard_layer_not_above(
            await self.layers.grants(SCOPE_GROUP, group.id),
            await self._actor_codes(actor),
            what="小组",
        )
        wanted = list(dict.fromkeys(user_ids))
        found = await self.users.get_many(wanted)
        if len(found) != len(wanted):
            raise UserNotFound(details={"unknown": sorted(set(wanted) - {u.id for u in found})})
        # 入组/退组 = 授权/撤权（见上面的守卫），所以差集要留痕
        before = set((await self.layers.members_for([group.id])).get(group.id, []))
        await self.layers.set_members(group.id, wanted)
        await self.session.commit()
        await self._audit_change(
            "identity.group.members",
            actor.id,
            target_type="group",
            target_id=group.id,
            detail={
                "added": sorted(set(wanted) - before),
                "removed": sorted(before - set(wanted)),
                "countTo": len(wanted),
            },
        )
        logger.info("管理员 %s 设置小组 %s 成员 %d 人", actor.id, group.name, len(wanted))
        return await self._group_out(group)

    async def set_group_permissions(
        self, group_id: str, permissions: Sequence[str], actor: CurrentUser
    ) -> GroupOut:
        group = await self._require_group(group_id)
        self._validate_permissions(permissions)
        current = await self.layers.grants(SCOPE_GROUP, group.id)
        actor_codes = await self._actor_codes(actor)
        self._guard_layer_not_above(current, actor_codes, what="小组")
        self._guard_permission_grant(
            permissions, actor_codes, what=f"小组 {group.name}"
        )
        await self.layers.set_grants(SCOPE_GROUP, group.id, permissions)
        await self.session.commit()
        await self._audit_change(
            "identity.group.permissions",
            actor.id,
            target_type="group",
            target_id=group.id,
            detail={"name": group.name, **_grant_diff(current, permissions)},
        )
        return await self._group_out(group)

    async def team_permissions(self) -> list[str]:
        return await self.layers.grants(SCOPE_TEAM, TEAM_SCOPE_ID)

    async def set_team_permissions(
        self, permissions: Sequence[str], actor: CurrentUser
    ) -> list[str]:
        self._validate_team_grants(permissions)
        current = await self.layers.grants(SCOPE_TEAM, TEAM_SCOPE_ID)
        # 团队层的地板（`permissions.TEAM_FLOOR_CODES`）。
        #
        # 为什么不能只靠 `_guard_layer_not_above`：那道守卫判的是「当前团队授权
        # ⊆ 操作者」，而每个人的有效权限**本来就把团队层并进去了**
        # （`resolve_identity` 无条件合并），所以它在团队层上结构性地恒真 ——
        # 一个只有 `identity:team:manage` 的委派管理员因此能把基础权限整组清空，
        # 全站非超管当场连 `task:read` 都没有。
        # 上层的增删仍然可以委派，只有「动地板」收回到超管。
        lost = sorted(
            code
            for code in TEAM_FLOOR_CODES
            if code in current and code not in permissions
        )
        if lost:
            self._require_superuser(actor, f"移除团队基础权限的地板项 {lost}")
        actor_codes = await self._actor_codes(actor)
        self._guard_layer_not_above(current, actor_codes, what="团队基础权限")
        self._guard_permission_grant(
            permissions, actor_codes, what="团队基础权限"
        )
        await self.layers.set_grants(SCOPE_TEAM, TEAM_SCOPE_ID, permissions)
        await self.session.commit()
        await self._audit_change(
            "identity.team.permissions",
            actor.id,
            target_type="team",
            target_id=TEAM_SCOPE_ID,
            detail=_grant_diff(current, permissions),
        )
        logger.info("管理员 %s 更新团队基础权限：%d 条", actor.id, len(permissions))
        return await self.team_permissions()

    async def user_permissions(self, user_id: str) -> list[str]:
        await self._require_user(user_id)
        return await self.layers.grants(SCOPE_USER, user_id)

    async def set_user_permissions(
        self, user_id: str, permissions: Sequence[str], actor: CurrentUser
    ) -> list[str]:
        user = await self._require_user(user_id)
        # 改别人的权限 = 授出/拿走访问权，和「改角色」走同一条红线
        actor_codes = await self._actor_codes(actor)
        await self._guard_target_not_above(user, actor_codes, actor)
        # 超管账号的权限恒为 `*`：个人层写进去也不生效。放在提权守卫之后 ——
        # 非超管碰超管该吃 403（说「你动不了」），超管自己才该看到「没有意义」。
        target_roles = (await self.users.roles_for([user.id])).get(user.id, ())
        if any(role.is_superuser for role in target_roles):
            raise SuperuserGrantForbidden(details={"username": user.username})
        self._validate_permissions(permissions)
        current = await self.layers.grants(SCOPE_USER, user.id)
        self._guard_layer_not_above(current, actor_codes, what="个人权限")
        self._guard_permission_grant(
            permissions, actor_codes, what=f"用户 {user.username}"
        )
        await self.layers.set_grants(SCOPE_USER, user.id, permissions)
        await self.session.commit()
        await self._audit_change(
            "identity.user.permissions",
            actor.id,
            target_type="user",
            target_id=user.id,
            detail={"username": user.username, **_grant_diff(current, permissions)},
        )
        return await self.user_permissions(user.id)

    async def set_permission_exceptions(
        self, user_id: str, exceptions: Sequence[str], actor: CurrentUser
    ) -> list[str]:
        """把「只对这个人关掉」的权限码整组替换掉。

        为什么不复用 `set_user_permissions`：那是**授予**方向，替换的是
        「他额外有什么」；这里是另一个方向。合成一个接口，调用方会以为
        传空数组等于清空他的权限，而它清的是**例外** —— 一次误读就是撤权。

        写了例外之后，同码的允许项会一起被清掉：库里同时留着「允许」和
        「否决」是两种表示，迟早有一处只认其中一种（本仓踩过三次）。
        清掉之后语义也干净：**恢复 = 删掉否决**，不需要记住原来给过什么。
        """
        user = await self._require_user(user_id)
        # 改别人的权限 = 授出/拿走访问权，和「改角色」走同一条红线
        actor_codes = await self._actor_codes(actor)
        await self._guard_target_not_above(user, actor_codes, actor)

        codes = list(dict.fromkeys(exceptions))
        self._validate_exceptions(codes)

        # 全量账号（超管角色，或直接被授了 `*`）：例外不会生效 —— 求值在
        # `*` 处短路。不能让它写进去变成一个「点了没反应」的开关。
        # 放在提权守卫之后：非超管碰超管该吃 403（「你动不了」），
        # 超管自己才该看到「没有意义」。
        identity = await self.resolve_identity(user.id, apply_page_gate=False)
        if identity is not None and SUPER_PERMISSION in identity.permissions:
            raise SuperuserGrantForbidden(details={"username": user.username})

        self._guard_permission_grant(
            codes, actor_codes, what=f"用户 {user.username} 的例外"
        )

        current = await self.layers.grants(SCOPE_USER, user.id, effect=EFFECT_DENY)
        await self.layers.set_grants(SCOPE_USER, user.id, codes, effect=EFFECT_DENY)

        allowed = await self.layers.grants(SCOPE_USER, user.id)
        kept = [code for code in allowed if code not in set(codes)]
        if kept != allowed:
            await self.layers.set_grants(SCOPE_USER, user.id, kept)

        await self.session.commit()
        await self._audit_change(
            "identity.user.permission_exceptions",
            actor.id,
            target_type="user",
            target_id=user.id,
            detail={"username": user.username, **_grant_diff(current, codes)},
        )
        return sorted(codes)

    async def user_permission_exceptions(self, user_id: str) -> list[str]:
        """这个账号的例外清单（供编辑入口直接读，不必拉整份总览）。"""
        user = await self._require_user(user_id)
        return await self.layers.grants(SCOPE_USER, user.id, effect=EFFECT_DENY)

    async def effective_permissions(self, user_id: str) -> EffectivePermissionsOut:
        """把四层来源摊开给界面看：每一行权限、它的来源、以及不生效的原因。

        **判定永远只看 `effective`**：前端不要拿 `personal` 去算「有没有权限」，
        那会把团队/小组/角色的授予全部漏掉。

        三组「不生效」的码分开报，因为界面对它们的动作不同：

        - `denied`：本人例外。**包含当前不生效的那些** —— 角色撤了但那条例外
          还在，它必须仍然可见、可撤销，否则就是一个谁也看不见的隐藏状态；
        - `deniedOff`：授予了、但因为本人例外而不生效（点「恢复」就好）；
        - `gatedOff`：授予了、但它所属的页面一页都没开（要去开页面）。

        后两者互斥，由 `core.authz.evaluate` 一次算出来 —— 一个码不生效只有
        这两种原因，混在一起管理员就只能猜。
        """
        user = await self._require_user(user_id)
        layers = await self.layers.layer_codes(user.id)
        groups = await self.layers.groups_by_ids(layers.group_ids)
        roles = tuple((await self.users.roles_for([user.id])).get(user.id, ()))
        role_perms = await self.roles.permissions_for([role.id for role in roles])
        role_codes = sorted(
            {code for role in roles for code in role_perms.get(role.id, ())}
        )
        codes = set(role_codes) | layers.all_codes()

        evaluation = evaluate(
            codes,
            layers.denies,
            is_superuser=any(role.is_superuser for role in roles),
        )

        # 影响面：改共享单元之前必须知道会连累多少人（这个数字猜不出来）。
        role_counts = await self.roles.user_counts([role.id for role in roles])
        group_counts = await self.layers.member_counts(list(layers.group_ids))

        return EffectivePermissionsOut(
            team=list(layers.team),
            groups=[
                GroupLayerOut(
                    id=gid,
                    name=groups[gid].name if gid in groups else gid,
                    permissions=list((layers.groups or {}).get(gid, ())),
                    # 除去本人：界面写的是「另有 N 人」
                    memberCount=max(0, group_counts.get(gid, 0) - 1),
                )
                for gid in layers.group_ids
            ],
            personal=list(layers.personal),
            roles=role_codes,
            pages=list(evaluation.pages),
            gatedOff=list(evaluation.gated_off),
            deniedOff=list(evaluation.denied_off),
            denied=list(layers.denies),
            effective=sorted(evaluation.effective),
            roleDetails=[
                RoleLayerOut(
                    id=role.id,
                    code=role.code,
                    name=role.name,
                    permissions=sorted(role_perms.get(role.id, ())),
                    userCount=max(0, role_counts.get(role.id, 0) - 1),
                )
                for role in roles
            ],
            sources=self._permission_sources(
                layers=layers, groups=groups, roles=roles, role_perms=role_perms
            ),
        )

    @staticmethod
    def _permission_sources(
        *,
        layers: LayerCodes,
        groups: dict[str, Group],
        roles: tuple[Role, ...],
        role_perms: Mapping[str, Sequence[str]],
    ) -> list[PermissionSourceOut]:
        """每个权限码由哪几层给到 —— 界面每一行的来源 chip 就是它。

        每一层都先**摊开通配**：角色给的是 `task:*`，而界面上的行是具体码，
        不摊就永远是「来源不明」。键取摊开后的码，与 `effective` / `gatedOff`
        里的码同一套写法，前端可以直接对齐。

        `kind` 用 scope 常量（team / group / user）加 `role`，不另造一套词：
        前后端对「层」的叫法必须一致，否则又要维护一张对照表。
        """
        index: dict[str, list[PermissionOriginOut]] = {}

        def add(codes: Iterable[str], origin: PermissionOriginOut) -> None:
            for code in expand_grants(codes):
                index.setdefault(code, []).append(origin)

        add(layers.team, PermissionOriginOut(kind=SCOPE_TEAM, name="团队"))
        for gid, group_codes in (layers.groups or {}).items():
            add(
                group_codes,
                PermissionOriginOut(
                    kind=SCOPE_GROUP,
                    id=gid,
                    name=groups[gid].name if gid in groups else gid,
                ),
            )
        for role in roles:
            add(
                role_perms.get(role.id, ()),
                PermissionOriginOut(kind="role", id=role.id, name=role.name),
            )
        add(layers.personal, PermissionOriginOut(kind=SCOPE_USER, name="个人"))

        return [
            PermissionSourceOut(code=code, layers=origins)
            for code, origins in sorted(index.items())
        ]

    # ------------------------------------------------------------------ #
    # 权限目录
    # ------------------------------------------------------------------ #
    def permission_catalog(self) -> PermissionCatalogOut:
        """权限目录（含页面 → 接口的关联）。

        页面项额外带 `requires`（它关联的接口码）与 `route`（它的路由），
        前端据此把两层关系画出来 —— 只靠描述文字（「关联接口：a, b, c」）
        让人自己拼是不行的，那正是「看不出层级」的根源。
        """
        groups = [
            PermissionGroupOut(group=group, items=[_permission_out(s) for s in items])
            for group, items in catalog.grouped().items()
        ]
        return PermissionCatalogOut(groups=groups, total=len(catalog))

    # ------------------------------------------------------------------ #
    # 启动引导
    # ------------------------------------------------------------------ #
    async def ensure_team_permissions(self, *, force: bool = False) -> int:
        """首次启动时把 `TEAM_BASE_PERMISSIONS` 播进团队层，返回新写入的条数。

        `force=True` 只给 `app seed --reset` 用：那是一条「把演示库清空重来」的命令，
        会按 metadata 删掉 `permission_grants` 的行，但 `app_meta`（框架记账表）
        刻意保留，于是快照标记还在、团队层却空了。不强制重播的话，
        重置后的库会得到一个「所有人都没有任何基础权限」的假状态。
        生产环境不跑 seed，管理员的编辑不会被这里覆盖。

        「团队 = 所有人」是一层**配置**，不是成员表：所有启用账号在
        `resolve_identity()` 里无条件合并 `team` 层，无需给每个用户写一行。

        为什么用 `app_meta` 标记而不是「看表里空不空」：管理员把基础权限
        全部清空是合法操作，用「空了就补」会在下次重启把他删掉的又加回来。
        标记只说明「这个版本已经播种过」，与管理员的后续编辑完全解耦。

        并发：两个实例同时启动时都读到「无标记」，各自补一遍（幂等），
        但只有一个能 CAS 写入标记；输的那个 `expire_all()` 后下次启动会重读，
        不会留下半套状态。
        """
        entry = await read_entry(self.session, TEAM_SNAPSHOT_KEY)
        if entry is not None and not force:
            return 0
        current = set(await self.layers.grants(SCOPE_TEAM, TEAM_SCOPE_ID))
        missing = [code for code in TEAM_BASE_PERMISSIONS if code not in current]
        if missing:
            await self.layers.set_grants(
                SCOPE_TEAM, TEAM_SCOPE_ID, sorted(current | set(missing))
            )
        written = await set_json(
            self.session,
            TEAM_SNAPSHOT_KEY,
            sorted(TEAM_BASE_PERMISSIONS),
            expected_version=0 if entry is None else entry.version,
            detail=f"团队基础权限播种：{len(missing)} 条",
        )
        if written:
            await self.session.commit()
            logger.info("团队基础权限已播种：%s", missing)
        else:
            # 别的实例抢先写了标记：丢掉旧会话状态，播种本身幂等，不必回滚
            self.session.expire_all()
            logger.info("团队基础权限快照被其他实例写入，本次跳过")
        return len(missing)

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

    async def sync_builtin_role_permissions(
        self, *, only: Iterable[str] | None = None, dry_run: bool = False
    ) -> dict[str, list[str]]:
        """把 `BUILTIN_ROLES` 里**缺失**的权限点补进已存在的内置角色，返回补了什么。

        为什么单独给一个命令，而不是让 `ensure_builtin_roles` 直接覆盖：
        那个函数「不覆盖已有权限」是**刻意**的（管理员手工调过的配置不该被重启改回去），
        把它改掉等于用一个副作用换一次方便。代价是新增权限点永远进不了老库——
        上一个模块就撞上了：`template:use` 写进了声明，库里的 editor 仍然没有，
        非超管上线即 403，而超管拿 `*` 全程自测发现不了。

        语义刻意收得很窄：
        - **只加不减**：管理员手工勾掉的权限不会被加回来（它已不在 `BUILTIN_ROLES` 里）；
        - **只补差集**：跑第二次什么都不做，可以放进部署脚本；
        - 角色本身不存在时跳过——那是 `ensure_builtin_roles` 的活，别在这里造半个角色。

        `only` 把范围再收窄一层：只补落在给定集合里的权限码。启动时用的是
        `sync_new_permissions`（只补这一版新增的），CLI 不传则保持全量补齐。

        `dry_run` 只算不写：升级前想知道「自动补齐会改动什么」时用它，
        比事后去角色界面逐条比对可靠。
        """
        added: dict[str, list[str]] = {}
        for spec in BUILTIN_ROLES:
            role = await self.roles.get_by_code(spec.code)
            if role is None:
                continue
            current = set((await self.roles.permissions_for([role.id])).get(role.id, []))
            missing = [
                code
                for code in spec.permissions
                if code not in current and (only is None or code in only)
            ]
            if not missing:
                continue
            added[spec.code] = missing
            if dry_run:
                continue
            await self.roles.set_permissions(role.id, sorted(current | set(missing)))
        if added and not dry_run:
            await self.session.commit()
            logger.info("内置角色权限已补齐：%s", added)
        return added

    async def sync_new_permissions(self, *, prune: bool = False) -> dict[str, list[str]]:
        """启动时把「这一版**新声明**的权限点」自动补进内置角色。

        与 `sync_builtin_role_permissions` 的差别只在**范围**：那个函数会补齐
        `BUILTIN_ROLES` 里所有缺失项，包括管理员手工去掉的，所以只能人工触发；
        这个函数只补「上一版还没声明过」的权限点，因此可以每次启动都跑。

        判定「新」靠 `app_meta` 里的目录快照（`permissions.catalog`）：

        - **没有快照** = 老库第一次跑到这一版 → 做一次全量补齐，把历史欠账还上。
          代价是这一次可能把管理员手工移除的旧权限加回来；只发生一次，写进
          `docs/OPS.md` 的升级步骤里。
        - **有快照** → 只补 `当前目录 - 快照` 的差集，管理员的删除从此不会再被覆盖。

        这解决了「上线新模块 → 非超管 403 → 超管自测发现不了」那条老路：
        它过去依赖「发版时记得跑 `app sync-roles`」这种人为动作。

        ## 与并发有关的两个约束

        **写入必须带上「我据以决策的版本」**（`expected_version`）。滚动发布时旧版本
        实例与新版实例会同时启动，两者的 `declared` 不同；谁最后写谁赢的话，旧实例
        能把快照退回旧集合，于是下一次启动把少掉的那几个当成「新增」又补进内置角色，
        管理员手工摘掉的权限被悄悄加回来。CAS 失败就重读、重算差集、重跑一次补齐
        （补齐本身幂等，多跑一次没有副作用）。

        **自动路径只增不减**（`declared | previous`）。「谁少写了」和「谁写得晚」
        在数据上分不清，而两种错的代价不对称：多留一个历史权限码只是少补一次，
        少留一个会撤销管理员的决定。要收缩（不再声明的权限点从快照里删掉）由人跑
        `app sync-roles --prune-snapshot`——明确动作、可解释、可审计。
        """
        declared = set(catalog.codes())
        if not declared:
            # 目录为空只可能是「模块没被发现」——例如从脚本里直接实例化这个服务，
            # 或者 CLI 忘了先 `discover_modules()`。这时候写快照等于把「这一版声明了
            # 哪些权限」清空，下一次启动就会把管理员手工移除的权限全当「新增」补回来，
            # 而且 `--prune-snapshot` 会把整张表抹掉。宁可什么都不做，也不能写空的。
            logger.error("权限目录为空，跳过权限同步与快照写入（模块未发现？）")
            return {}
        added: dict[str, list[str]] = {}
        for attempt in range(1, WRITE_RETRIES + 1):
            entry = await read_entry(self.session, PERMISSION_SNAPSHOT_KEY)
            previous = None if entry is None else [str(code) for code in entry.value]
            if previous is None:
                added = await self.sync_builtin_role_permissions()
                logger.info("权限目录首次快照：%d 个权限点，已做一次全量补齐", len(declared))
            else:
                fresh = declared - set(previous)
                added = await self.sync_builtin_role_permissions(only=fresh) if fresh else {}
                if fresh:
                    logger.info("新增权限点 %s 已并入内置角色：%s", sorted(fresh), added)

            target = sorted(declared) if prune else sorted(declared | set(previous or ()))
            if previous == target:
                return added
            written = await set_json(
                self.session,
                PERMISSION_SNAPSHOT_KEY,
                target,
                expected_version=0 if entry is None else entry.version,
                detail=_snapshot_detail(previous or [], target),
            )
            if written:
                await self.session.commit()
                return added
            # 别的实例在这一瞬间写过：丢掉会话里的旧值，下一轮读到的必须是新的
            logger.info(
                "权限目录快照被其他实例改写（第 %d/%d 轮），重读后重新判断",
                attempt,
                WRITE_RETRIES,
            )
            self.session.expire_all()

        logger.warning(
            "权限目录快照连续 %d 轮被其他实例改写，本次跳过；下次启动会重做（补齐是幂等的）",
            WRITE_RETRIES,
        )
        return added

    async def prune_stale_permissions(self) -> dict[str, list[str]]:
        """删掉「权限目录里已经不存在」的码，返回删了什么。

        为什么必须有这个「只减」的搭档（真实库实测逼出来的）：

        `set_grants` / `set_role_permissions` 都是**整组替换**，而写入口会先用
        `_validate_permissions` 挡未知码。于是库里只要还躺着一个已下线的码
        （v1 素材库下线留下的 `asset:read`、改名前留下的 `template:read` 都是），
        那一层就**永远保存不了**：前端把现有码原样提交 → 400「存在无法识别的
        权限点」→ 而 doctor 的提示恰恰是「可在『用户与权限』页重新保存清理」，
        这句话在数据脏的时候做不到，形成死锁。

        清理是安全的，因为「未知码」在本仓库里可证明是惰性的：
        `test_architecture` 保证 `require(...)` 用到的码必须有人声明，
        没声明的码不可能被任何路由判定；它唯一的作用是把配置界面卡住。

        与 `sync_new_permissions`（只加）配对，一加一减都放在启动引导里；
        两者都幂等，多实例同时跑只会有一个删到行。删除动作本身进审计，
        因为「谁的权限悄悄变了」必须能回答。
        """
        known = catalog.codes()
        resources = {code.split(":", 1)[0] for code in known}

        def stale(codes: Iterable[str]) -> list[str]:
            return sorted(
                code
                for code in set(codes)
                if not _known_permission(code, known, resources)
            )

        layer_codes = stale(await self.layers.all_grant_codes())
        role_codes = stale(await self.roles.permission_codes())
        if not layer_codes and not role_codes:
            return {"layers": [], "roles": []}

        dropped_layers = await self.layers.drop_codes(layer_codes) if layer_codes else 0
        dropped_roles = (
            await self.roles.drop_permission_codes(role_codes) if role_codes else 0
        )
        await self.session.commit()
        logger.warning(
            "已清理下线权限码：三层授权 %d 行 %s；角色权限 %d 行 %s",
            dropped_layers,
            layer_codes,
            dropped_roles,
            role_codes,
        )
        await audit.record(
            "identity.permissions.prune",
            actor_id=None,
            target_type="permission_catalog",
            target_id="",
            detail={
                "layers": layer_codes,
                "roles": role_codes,
                "droppedLayerRows": dropped_layers,
                "droppedRoleRows": dropped_roles,
            },
        )
        return {"layers": layer_codes, "roles": role_codes}

    async def migrate_roles_to_pages(self) -> dict[str, list[str]]:
        """给「有接口码、却没有对应页面码」的授权补上页面码。

        为什么需要：页面门控让「接口权限」依赖「页面被授予」。
        老库里的自定义角色 / 小组授权 / 个人授权只有接口码 → 升级之后这些授权
        **全部失效**（人打开页面一片 403），而配置界面看上去完全正常 ——
        这是最坏的一种升级事故：功能坏了，但没人知道该改哪里。

        迁移按「这些接口码属于哪些页面」补齐，等价于**保持升级前的可用性**。

        ## 为什么是增量的，而不是「一次性」

        一次性迁移有个漏洞：**这一版新声明的页面关系不会被补到**。
        比如某模块这一版才把 `model_driver:read` 关联到「任务中心」页，
        老库里持有这个码的角色就会突然失效 —— 升级前它能用，升级后 403，
        而配置界面看上去完全正常。

        所以判据不是「这个库迁移过没有」，而是「**哪些接口码是这一版才开始
        受门控的**」：拿当前的「接口码 → 声明它的页面」映射与上一版存档的
        映射求差，只对新增的那些码做补齐。

        这样有两层保障：

        - 管理员后来特意关掉的页面**不会**被重新打开 —— 那些码不是新增的，
          不在差集里；
        - 之后每加一个页面声明，都会有一次对应的补齐，不会攒到下个版本。

        补不全的接口（没有任何页面声明它）本来就不受门控，不用管。
        """
        entry = await read_entry(self.session, PAGES_MIGRATION_KEY)
        previous: dict[str, list[str]] | None = None
        if entry is not None:
            raw = entry.value
            if isinstance(raw, dict):
                previous = {
                    str(code): [str(page) for page in pages]
                    for code, pages in raw.items()
                }
            # 旧格式（迁移跑过时存的是「补过的授权键」列表）→ 视为没有映射存档。
            # 那正是「从一次性迁移时代的老库升级过来」的场景：全部受门控的码
            # 都当作新增，重新补一遍 —— 与旧的一次性迁移行为一致。

        # 当前的「接口码 → 声明它的页面」映射
        # 映射由接口侧声明生成（`PermissionSpec.pages`），见 `core.pages` 的模块文档。
        current: dict[str, list[str]] = {
            code: list(pages) for code, pages in page_catalog.gated().items()
        }

        # 本次要补的 = 这一版才开始受门控的码。
        # 首次运行（没有存档）时，全部受门控的码都算「新增」——
        # 那正是「从没有页面权限的老库升级过来」的场景。
        if previous is None:
            newly_gated = set(current)
        else:
            newly_gated = {
                code
                for code, pages in current.items()
                if code not in previous or set(previous[code]) != set(pages)
            }

        if not newly_gated:
            # 没有新增受门控的码：把当前映射存档下来即可，不动任何授权。
            await self._save_pages_migration(entry, current)
            return {}

        added: dict[str, list[str]] = {}

        def missing_pages(codes: Iterable[str], covered: set[str]) -> list[str]:
            """这些码里，哪些需要补页面码；补哪些。

            判据是「**这个码一个可用页面都没有**」：
            门控规则是「至少一个声明它的页面被授予」，所以只要已有一个声明它的
            页面在手上，这个码就仍然有效 —— 不需要再补。

            这个判据同时避免**过度授权**：一个码常被多个页面声明
            （`library:read` ← 资产库 + 模板封装）。若无条件把声明它的页面
            全补上，一个只配了「资产库」的角色会顺带拿到「模板封装」——
            那是凭空多给一页的可见性，不是「保持升级前的可用性」。

            只补一个可用页面都没有的码，正是**保持升级前的可用性**：
            升级前这个角色能用这个接口，升级后也还能用。
            """
            pages: set[str] = set()
            for code in codes:
                if code not in newly_gated:
                    continue  # 不是这一版新增受门控的：管理员配过了，别动
                owners = page_catalog.pages_for(code)
                if not owners or (set(owners) & covered):
                    continue  # 已有可用页面 → 仍然有效
                pages.update(owners)
            return sorted(pages)

        # ① 角色权限
        roles = list(await self.roles.list_all())
        role_perms = await self.roles.permissions_for([role.id for role in roles])
        for role in roles:
            codes = list(role_perms.get(role.id, ()))
            if not codes:
                continue
            covered = {code for code in codes if code.startswith(PAGE_PREFIX)}
            pages = missing_pages(codes, covered)
            if not pages:
                continue
            merged = sorted(set(codes) | set(pages))
            await self.roles.set_permissions(role.id, merged)
            added[role.code] = pages

        # ② 三层授权（小组 / 个人）
        #
        # **团队层跳过**：团队 = 所有启用账号，而且这一层没有 deny ——
        # 把页面码放进团队层等于「这一页对所有人永远关不掉」，
        # 正好废掉页面权限本身（写接口也是这么挡的，见 TEAM_FORBIDDEN_PREFIXES）。
        by_scope: dict[tuple[str, str], list[str]] = {}
        for scope, scope_id, permission in await self.layers.all_grants():
            if scope == SCOPE_TEAM:
                continue
            by_scope.setdefault((scope, scope_id), []).append(permission)
        for (scope, scope_id), codes in by_scope.items():
            covered = {code for code in codes if code.startswith(PAGE_PREFIX)}
            pages = missing_pages(codes, covered)
            if not pages:
                continue
            merged = sorted(set(codes) | set(pages))
            await self.layers.set_grants(scope, scope_id, merged)
            added[f"{scope}:{scope_id}"] = pages

        await self._save_pages_migration(entry, current, added)
        return added

    async def _save_pages_migration(
        self,
        entry: Entry | None,
        current: dict[str, list[str]],
        added: dict[str, list[str]] | None = None,
    ) -> bool:
        """把当前的「接口码 → 页面」映射存档，并写一条人能看懂的审计。

        存的是**映射本身**而不是「迁移过没有」：下一版要靠它求差，
        才知道哪些码是这一版才开始受门控的。
        """
        detail = (
            f"页面门控映射更新：{len(current)} 个接口码受门控，"
            f"{len(added or {})} 处授权补了页面码"
        )
        written = await set_json(
            self.session,
            PAGES_MIGRATION_KEY,
            current,
            expected_version=0 if entry is None else entry.version,
            detail=detail,
        )
        if written:
            await self.session.commit()
            if added:
                logger.info("页面权限迁移：%d 处授权补上了页面码 %s", len(added), added)
        else:  # pragma: no cover - 别的实例抢先写了标记
            self.session.expire_all()
        return written

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
                display_name=display_name or username,
                is_active=True,
            )
            await self.set_password(user, password)
            self.session.add(user)
            await self.session.flush()
        else:
            await self.set_password(user, password)
            user.is_active = True

        current = set(await self.users.role_ids_of(user.id))
        if role.id not in current:
            await self.users.set_roles(user.id, [*current, role.id])
        await self.session.commit()
        return user, created

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    async def _cut_access(self, user: User, *, reason: str) -> int:
        """切断一个账号的访问权：**置停用 + 吊销全部会话**，唯一写点。

        为什么两件事必须绑在一起（R10-02）：`is_active` 只管「请求进不进得来」，
        管不了「票还在不在」。停用期间令牌被拒，但会话行的 `revoked_at` 仍是
        `None`、`expires_at` 仍是登录时算的 7 天后——管理员重新启用（误判纠正、
        临时冻结解除）时，解析器的四个条件重新全部满足，**同一张 access 与
        refresh 原地复活**，连口令都不需要。`refresh` 寿命 7 天且滑动续期，
        所以「停用」在产品语义上必须是切断，而不是暂停。

        写成唯一入口而不是在两个调用点各补一句：这轮的问题本质就是
        「同一个不变量有两个写点，两个都漏了」。
        """
        user.is_active = False
        revoked = await self.sessions.revoke_all(user.id, reason=reason)
        await self.session.flush()
        return revoked

    async def set_password(self, user: User, raw: str) -> None:
        """**唯一**的口令写入点。

        写口令必须同时更新 `password_changed_at`：解析器靠它让该账号此前签发的
        所有令牌失效。改密（自助 / 管理员重置 / `create-admin` 轮换）、建号、
        演示数据装载全部走这里——上一轮漏掉这条不变式，正是「超管疑似泄漏、
        运维紧急轮换口令后攻击者手里的令牌还能用满 2 小时」的原因（R5-02 / R4-13）。

        哈希本身在线程池里算（`hash_password_async`）：150ms 的纯 CPU 工作，
        留在事件循环里会让全进程停摆（R6-02）。所以这个方法是 `async` 的——
        签名不同不是风格问题，而是「它会等待」这件事必须写在类型上。
        """
        user.password_hash = await hash_password_async(raw)
        user.password_changed_at = utcnow()

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
        """按传入顺序返回角色，缺失任何一个就报错（而不是静默少挂一个）。

        一条 SQL 取全量再本地比对：逐 id `await` 是 N+1，5 个角色 5 次往返，
        而这里每次都在「改权限」的写路径上（R6-07）。
        """
        if not role_ids:
            return []
        unique = list(dict.fromkeys(role_ids))
        found = {role.id: role for role in await self.roles.get_many(unique)}
        missing = [role_id for role_id in unique if role_id not in found]
        if missing:
            raise RoleNotFound(f"角色不存在：{', '.join(missing)}")
        return [found[role_id] for role_id in unique]

    def _validate_permissions(self, codes: Sequence[str]) -> None:
        known = catalog.codes()
        resources = {code.split(":", 1)[0] for code in known}
        invalid = [code for code in codes if not _known_permission(code, known, resources)]
        if invalid:
            raise UnknownPermission(
                f"存在无法识别的权限点：{', '.join(sorted(set(invalid)))}",
                details={"invalid": sorted(set(invalid))},
            )

    def _validate_exceptions(self, codes: Sequence[str]) -> None:
        """例外只能作用于「具体的、可管理的」权限码。

        三条拒绝理由各不相同，所以分开报：

        - 目录里没有这个码 → `UnknownPermission`（拼错，或模块已下线）；
        - 通配（`task:*` / `*`）→ 没有唯一解释，见 `ExceptionWildcardForbidden`；
        - `identity:*` → 会把自己锁在门外，见 `ExceptionLockedPermission`。
        """
        self._validate_permissions(codes)
        wildcard = [code for code in codes if code == "*" or code.endswith(":*")]
        if wildcard:
            raise ExceptionWildcardForbidden(details={"codes": sorted(wildcard)})
        locked = [code for code in codes if code.startswith("identity:")]
        if locked:
            raise ExceptionLockedPermission(details={"codes": sorted(locked)})

    # ---------- 提权面（四个入口共用，别再各写一遍） ---------- #
    @staticmethod
    def _require_superuser(actor: CurrentUser, reason: str) -> None:
        if not actor.has(SUPER_PERMISSION):
            raise SuperuserRequired(f"{reason}：只有超级管理员可以操作")

    @staticmethod
    def _missing_grants(actor_codes: set[str], codes: Iterable[str]) -> list[str]:
        """`codes` 里调用者自己没有的那些。

        `actor_codes` 是**摊开过通配、减过例外**的原始集合（见 `_actor_codes`）。
        请求里可能是通配（`task:*`），所以两边都摊成具体码再比：
        「能授出 `task:*`」= 它下面每一个动作我都具备。

        以前这里用 `permission_holds(actor_codes, code)`：那时 `actor_codes` 里
        还留着 `task:*` 这种通配，比得动；摊开之后留着通配反而会让
        「授出 `task:*`」被判成缺权限，所以关系改成正向包含。
        """
        if SUPER_PERMISSION in actor_codes:
            return []
        missing = [code for code in codes if not expand_grants({code}) <= actor_codes]
        return sorted(set(missing))

    async def _actor_codes(self, actor: CurrentUser) -> set[str]:
        """操作者的**原始**授权集合（没过页面门控，但摊开了通配、减了例外）。

        为什么授权判据必须用原始集合：页面门控是「可见性」层的过滤，
        不是「授权层级」的改变。一个只有 `identity:team:manage` 的委派管理员，
        团队层那些因为「没有对应页面」而被门控掉的码，仍然是他**拥有**的授权；
        拿被门控后的集合去比「能不能动比自己高的东西」，会得出
        「他连团队基础权限一条都动不了」的怪结论（实测即如此）。

        所以：**能不能用**看门控后的集合（`CurrentUser.permissions`），
        **能不能授权**看原始集合（这里）。两层各管一件事，别混。
        """
        if actor.has(SUPER_PERMISSION):
            return {SUPER_PERMISSION}
        identity = await self.resolve_identity(actor.id, apply_page_gate=False)
        return set(identity.permissions) if identity else set(actor.permissions)

    def _guard_permission_grant(
        self, codes: Sequence[str], actor_codes: set[str], *, what: str
    ) -> None:
        missing = self._missing_grants(actor_codes, codes)
        if missing:
            raise PermissionEscalation(
                f"不能给{what}授予自己不具备的权限",
                details={"missing": missing},
            )

    async def _guard_role_grant(
        self, roles: Sequence[Role], actor_codes: set[str], actor: CurrentUser
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
                perms.get(role.id, ()), actor_codes, what=f"角色 {role.code}"
            )

    async def _guard_target_not_above(
        self, target: User, actor_codes: set[str], actor: CurrentUser
    ) -> None:
        """不能对「权限高于自己」的账号做写操作（重置口令 / 停用 / 改角色 / 改邮箱）。

        否则 `identity:update` 与 `identity:delete` 就成了接管超管的两条捷径。

        判据是「目标的权限集 ⊆ 操作者」——这是为**授出访问权**的动作设计的：
        重置口令 = 拿到对方身份，停用 = 拿走对方访问权，改角色 = 改对方权限。
        只涉及可见性的动作不属于这一类：改展示字段（昵称 / 头像）用
        `_guard_display_target`，读别人的登录审计用 `_guard_audit_target`。
        把这条套上去会让委派角色连昵称都改不了、连普通操作员的审计都读不了（R9-02 / R10-05）。
        """
        identity = await self.resolve_identity(target.id, apply_page_gate=False)
        if identity is None:  # pragma: no cover - 调用前已确认用户存在
            return
        if identity.is_superuser:
            self._require_superuser(actor, f"操作超级管理员账号 {target.username}")
            return
        missing = self._missing_grants(actor_codes, identity.permissions)
        if missing:
            raise PermissionEscalation(
                f"不能操作权限高于自己的账号 {target.username}",
                details={"missing": missing},
            )

    async def _guard_visible_target(
        self, target: User, actor: CurrentUser, *, action: str
    ) -> None:
        """「不涉及授权、只涉及可见性」的守卫：挡住超管就够了。

        两个调用方——改展示字段（`_guard_display_target`）与读别人的登录审计
        （`_guard_audit_target`）——判据相同、理由不同，判定只写这一份。
        用 `resolve_identity` 判超管而不是只看 `role.is_superuser` 标记：
        角色带着字面量 `*` 时也是超管等价，只认标记会让它从这里漏过去。
        """
        identity = await self.resolve_identity(target.id)
        if identity is None:  # pragma: no cover - 调用前已确认用户存在
            return
        if identity.is_superuser:
            self._require_superuser(actor, f"{action}超级管理员账号 {target.username}")

    async def _guard_display_target(self, target: User, actor: CurrentUser) -> None:
        """改别人的**展示字段**（昵称 / 头像）时的守卫：只判「目标是不是超管」。

        为什么不用 `_guard_target_not_above`（R9-02）：那个守卫的判断依据是
        「目标的权限集 ⊆ 操作者」，这是为**授出访问权**的动作设计的（重置口令 =
        拿到对方身份，停用 = 拿走对方访问权，改角色 = 改对方权限）。
        套在昵称上就变成「要改一个显示字符串，先得持有对方的一切业务权限」——
        一个只带 `identity:read` + `identity:update` 的委派角色因此连 viewer 的
        昵称都改不了，这个权限点在界面上承诺的能力和实际能力不是一回事。

        展示字段的真实风险是**冒充**：把昵称改成某个审批人的名字，让「谁批的」
        在列表里看起来不一样。所以只要挡住「比自己更有权威的目标」——超管——
        就够；其余交给 `identity:update` 这个权限点本身做第一道控制。
        头像 URL 的回连风险同理：能被改的账号，其资料的可见范围本来就不受它自己控制。
        """
        await self._guard_visible_target(target, actor, action="操作")

    async def _guard_audit_target(self, target: User, actor: CurrentUser) -> None:
        """读别人登录审计时的守卫（R10-05）：同样只挡超管。

        这里刻意没有照搬「与踢下线同级」的 `_guard_target_not_above`：那条判据是
        「目标的权限集 ⊆ 操作者」，为**授出访问权**的动作设计。套在读审计上，一个
        专职审计员（只有 `identity:read`）必须先持有被审计者的全部业务权限，
        否则连一个 `task:read` 的操作员都查不了——审计功能对「审计员」这个角色
        直接失效，R9-02 已经判定过这种一刀切：把功能换成了「看起来安全但没人能用」。

        真实风险是**读到比自己更有权威的人的网络与作息**：超管的登录 IP / UA /
        「凌晨三点有一次失败登录」本身就是要保护的安全事件素材。挡住超管即可，
        其余交给 `identity:read` 做第一道控制（用户管理页面本来就不含 IP）。
        """
        await self._guard_visible_target(target, actor, action="查看")

    async def _guard_role_not_above(
        self, role: Role, actor_codes: set[str], actor: CurrentUser
    ) -> None:
        """不能动「权限高于自己」的角色（删角色 / 改角色权限）。

        与 `_guard_target_not_above` 是同一条红线，只是对象从「账号」换成「角色」：
        否则 `identity:role:manage` 一个人就能把 `editor`（含 `task:*`）这类角色
        删掉或清空，而持有该角色的账号本身受保护——同一件事换个入口就绕过去了。
        """
        if role.is_superuser:
            self._require_superuser(actor, f"操作超级管理员角色 {role.code}")
            return
        perms = (await self.roles.permissions_for([role.id])).get(role.id, ())
        missing = self._missing_grants(actor_codes, perms)
        if missing:
            raise PermissionEscalation(
                f"不能操作权限高于自己的角色 {role.code}",
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
    """模块启动钩子：建内置角色 + 把新声明的权限点并进去。

    表不存在时明确报错，而不是静默继续：一个「没有角色」的身份库看起来能跑，
    实际所有鉴权都会 403，那种故障比启动失败难查得多。
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            service = IdentityService(session)
            await service.ensure_builtin_roles()
            # 团队基础权限只播一次；之后管理员的编辑是唯一权威（见方法注释）
            await service.ensure_team_permissions()
            # 这一步过去是人工的 `app sync-roles`：忘了跑就是「功能上线了，
            # 非超管点按钮 403，而超管自测全绿」。现在只在启动时补**新增**的
            # 权限点，管理员手工调过的配置不受影响（见 sync_new_permissions）。
            await service.sync_new_permissions()
            # 与「只加」配对的「只减」：把已下线模块留下的权限码从三层授权与
            # 角色权限里清掉。不清理的话，那些层会永远保存不了（见方法注释）。
            # 页面权限的一次性迁移：老库的自定义角色只有接口码，升级后会全部失效
            await service.migrate_roles_to_pages()
            await service.prune_stale_permissions()
        except SQLAlchemyError as exc:
            raise RuntimeError(
                "内置角色初始化失败：身份相关数据表可能尚未创建。"
                "生产环境请先执行 `alembic upgrade head`；开发环境检查 DB_URL。"
            ) from exc


__all__ = ["IdentityService", "ResolvedIdentity", "bootstrap"]
