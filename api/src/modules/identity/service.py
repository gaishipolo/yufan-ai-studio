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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.context import SUPER_PERMISSION, CurrentUser
from core.exceptions import Unauthorized
from core.logging import get_logger
from core.permissions import catalog
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
from db.app_meta import get_json, set_json
from db.base import utcnow
from db.session import get_session_factory
from modules.identity import policy, throttle
from modules.identity.errors import (
    AccountDisabled,
    BuiltinRoleLocked,
    CannotTargetSelf,
    CredentialInvalid,
    LastSuperuser,
    OldPasswordMismatch,
    PermissionEscalation,
    RefreshTokenInvalid,
    RoleCodeTaken,
    RoleInUse,
    RoleNotFound,
    SessionNotFound,
    SuperuserRequired,
    UnknownPermission,
    UsernameTaken,
    UserNotFound,
)
from modules.identity.models import LoginEvent, Role, User, UserSession
from modules.identity.permissions import BUILTIN_ROLES, SUPERUSER_ROLE
from modules.identity.repository import (
    LoginEventRepository,
    RoleRepository,
    SessionRepository,
    UserRepository,
)
from modules.identity.schema import (
    LoginEventListOut,
    LoginEventOut,
    MeOut,
    PasswordChangeIn,
    PasswordPolicyOut,
    PermissionCatalogOut,
    PermissionGroupOut,
    PermissionOut,
    ProfileUpdateIn,
    RoleBriefOut,
    RoleCreateIn,
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
_PROFILE_FIELDS = ("display_name", "email", "avatar_url")

# 但这份清单里的字段**风险等级不同**，守卫不能一刀切（R9-02）：
# - 展示字段（昵称 / 头像）：只影响渲染，要防的是「冒充比自己更有权威的人」，
#   判「目标是不是超管」就够。之前复用「目标权限 ⊆ 操作者」的严格守卫，结果是
#   一个只带 `identity:read` + `identity:update` 的委派角色连 viewer 的昵称都改不了
#   （40323）——把「能用的功能」换成了「看起来安全但没人能用」。
# - 找回入口（邮箱）：接上「邮箱找回密码 / 收验证码」之后，改它就是接管账号的第一步，
#   所以沿用「目标权限不得高于自己」的严格守卫（R7-03）。
_DISPLAY_FIELDS = ("display_name", "avatar_url")
_RECOVERY_FIELDS = ("email",)

# `/me/sessions` 的返回上限。会话表只增不减，列表不截断的话，一个用了一年多的
# 账号打开「我的设备」会一次拿到几百条（R10-07）。超出的按登录时间倒序丢弃——
# 前端本来也只在弹窗里列最近几台。
SESSION_LIST_LIMIT = 50

# 权限目录快照在 `app_meta` 里的键。存的是「上一次启动时目录里有哪些权限码」，
# 用来区分「新声明的权限点」和「管理员手工删掉的旧权限点」——见 `sync_new_permissions`。
PERMISSION_SNAPSHOT_KEY = "permissions.catalog"

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


def _epoch(value: Any) -> int:
    """datetime → epoch 秒；naive 按 UTC 解释（sqlite 读回来没有时区）。"""
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


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
        self.sessions = SessionRepository(session)
        self.logins = LoginEventRepository(session)

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
        key = f"{username}|{client_ip}"
        wait = throttle.penalty(key)
        if wait:
            await asyncio.sleep(wait)

        async with password_slot():
            user = await self.users.get_by_username(username)

            # 停用账号在**验密之前**就拒掉（R10-04）。之前这一步在验密之后，
            # 而且前面先 `throttle.reset(key)`，于是有两个后果：
            # 1) 403 与 401 的差异等于「口令猜对了」——对已知/疑似停用的账号
            #    （离职员工、被冻结的可疑账号）可以慢速验证口令，而这类账号
            #    恰恰是攻击者最想确认的；
            # 2) 对口令的路径每次都把计数清零，403 永远不会变成 429，
            #    等于可以无限次验证，成本只有攻击者自己的 CPU。
            # 代价是「账号存在且被停用」由 403 可见——这个信息本来就要给用户看
            # （登录页那句「账号已停用，请联系管理员」），不是泄漏。
            # 也正因为状态码已经说明了这件事，早退导致的计时差不再多泄漏什么。
            if user is not None and not user.is_active:
                throttle.record_failure(key)
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
                throttle.record_failure(key)
                locked = throttle.is_locked(key)
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
                throttle.reject(key)
                raise CredentialInvalid()

            throttle.reset(key)

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
            raise RefreshTokenInvalid()

        identity = await self.resolve_identity(user_id)
        if identity is None or not identity.user.is_active:
            raise RefreshTokenInvalid()
        # 与 access 令牌同一套判定：改密之前签发的票一律作废。
        # 不做这一步的话，攻击者手里那张 refresh 能在「改密自救」之后继续换新令牌，
        # 前面那套 `password_changed_at` 就只防住了一半。
        issued_at = int(claims.get("iat") or 0)
        if issued_at and issued_at < _epoch(identity.user.password_changed_at):
            raise RefreshTokenInvalid()

        session.refresh_jti = _new_jti()
        session.last_seen_at = utcnow()
        # 滑动延长：活跃用户不该在第 7 天被强制下线；长期不用的会话则按时过期
        session.expires_at = utcnow() + timedelta(
            seconds=settings.refresh_token_ttl_seconds
        )
        if user_agent:
            session.user_agent = _clip(user_agent, 256)
        if ip:
            session.ip = _clip(ip, 64)
        await self.session.commit()
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
        await self._guard_target_not_above(user, actor)
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
        # 整段在名额里：`set_password` 要算一次 scrypt，而这条路径从第一句起就要查库。
        # 先排队、再拿连接，才不会出现「几十个建号请求各自握着一条连接等哈希」。
        async with password_slot():
            if await self.users.get_by_username(payload.username) is not None:
                raise UsernameTaken()

            roles = await self._roles_by_ids(payload.role_ids)
            # 建号时直接挂超管角色 = 造一个自己登进去就是超管的账号，
            # 必须和 set_user_roles 走同一道闸（R4-11 路径 C）
            await self._guard_role_grant(roles, actor)
            user = User(
                username=payload.username,
                display_name=payload.display_name or payload.username,
                email=payload.email,
                avatar_url=payload.avatar_url,
                is_active=True,
            )
            # 建号也要写 `password_changed_at`：不走同一个方法就会出现「这个账号
            # 的口令没有建立时间」的第四种状态，而令牌失效判定依赖它
            await self.set_password(user, payload.password)
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
                await self._guard_target_not_above(user, actor)
            elif writing_display:
                await self._guard_display_target(user, actor)

        # `is_active` 的三种取值必须分开：**没传**（不动）/ true / false。
        # 用 `in fields` 会让「传了 null」走进 bool(None) = False（R6-03），
        # schema 已经挡了一次 422，这里再挡一次是为了内部调用（脚本、CLI）绕不过去。
        # 停用与恢复**两个方向**都要过守卫（R6-04）：安全事件后「先停用、再启用」，
        # 如果启用不需要超管，一个中级管理员就能把刚被停用的内鬼账号放回来。
        if writing_active:
            active = bool(fields["is_active"])
            if active is False and user.id == actor.id:
                raise CannotTargetSelf()
            if active:
                user.is_active = True
            else:
                # 停用**只能**走 `_cut_access`：置 `is_active` 与吊销会话是同一件事，
                # 两条停用路径（DELETE / PATCH）各写一半，就又回到 R10-02。
                await self._cut_access(user, reason="deactivate")

        for field in _PROFILE_FIELDS:
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
        # 撤权方向与授予方向必须对等：改别人的角色集合，等于改别人的权限，
        # 而「不能操作权限高于自己的账号」这条红线在改密 / 停用上已经写了，
        # 这里漏掉就出现「给不了、但能撤」——role:manage 一个人能把 editor 清空（R5-03）。
        # 放在授予检查之前：目标高于自己时先报「越级」，而不是先报「子集不满足」。
        await self._guard_target_not_above(user, actor)
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

        闸门必须在 `_require_user` **之前**：先把名额拿到再借连接。放在守卫之后就
        成了「先借连接、再排队」，20 并发能把连接池顶满（R9-01）——同一份文件里的
        `create_user` 就是正确写法，两处不该相反。守卫在闸门内跑只多占一个名额
        （`resolve_identity` 是几条短查询，不涉及 scrypt），峰值仍是闸门容量。
        """
        async with password_slot():
            user = await self._require_user(user_id)
            await self._guard_target_not_above(user, actor)
            await self.set_password(user, new_password)
            # 顺手吊销目标的所有会话：改密本身已经让旧 access 失效
            # （`password_changed_at`），但「会话列表里还挂着几台设备」会让管理员
            # 以为没踢干净；而这张表存在的意义就是「谁的会话还在」要看得见。
            await self.sessions.revoke_all(user.id, reason="password_reset")
            await self.session.commit()
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
        await self._guard_target_not_above(user, actor)
        revoked = await self._cut_access(user, reason="deactivate")
        await self._guard_has_superuser()
        await self.session.commit()
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
        # 与 set_user_roles 同一条红线：比自己强的角色不能被"顺手改成只读"，
        # 那和把人降级是一回事，只是换了个入口（R5-03 的同族）。
        await self._guard_role_not_above(role, actor)
        self._guard_permission_grant(permissions, actor, what=f"角色 {role.code}")
        await self.roles.set_permissions(role.id, permissions)
        await self.session.commit()
        return await self.get_role(role.id)

    async def delete_role(self, role_id: str, actor: CurrentUser) -> None:
        role = await self._require_role(role_id)
        if role.is_builtin:
            raise BuiltinRoleLocked()
        # 删掉一个比自己强的角色 = 让所有持有它的人瞬间掉权限，
        # 而角色本身没有 owner 字段可以判断"归谁管"（R5-06）
        await self._guard_role_not_above(role, actor)
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

    async def sync_new_permissions(self) -> dict[str, list[str]]:
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
        """
        declared = set(catalog.codes())
        previous = await get_json(self.session, PERMISSION_SNAPSHOT_KEY)
        if previous is None:
            added = await self.sync_builtin_role_permissions()
            logger.info("权限目录首次快照：%d 个权限点，已做一次全量补齐", len(declared))
        else:
            fresh = declared - {str(code) for code in previous}
            added = await self.sync_builtin_role_permissions(only=fresh) if fresh else {}
            if fresh:
                logger.info("新增权限点 %s 已并入内置角色：%s", sorted(fresh), added)
        current = sorted(declared)
        if previous != current:
            await set_json(self.session, PERMISSION_SNAPSHOT_KEY, current)
            await self.session.commit()
        return added

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
        """不能对「权限高于自己」的账号做写操作（重置口令 / 停用 / 改角色 / 改邮箱）。

        否则 `identity:update` 与 `identity:delete` 就成了接管超管的两条捷径。

        判据是「目标的权限集 ⊆ 操作者」——这是为**授出访问权**的动作设计的：
        重置口令 = 拿到对方身份，停用 = 拿走对方访问权，改角色 = 改对方权限。
        只涉及可见性的动作不属于这一类：改展示字段（昵称 / 头像）用
        `_guard_display_target`，读别人的登录审计用 `_guard_audit_target`。
        把这条套上去会让委派角色连昵称都改不了、连普通操作员的审计都读不了（R9-02 / R10-05）。
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

    async def _guard_role_not_above(self, role: Role, actor: CurrentUser) -> None:
        """不能动「权限高于自己」的角色（删角色 / 改角色权限）。

        与 `_guard_target_not_above` 是同一条红线，只是对象从「账号」换成「角色」：
        否则 `identity:role:manage` 一个人就能把 `editor`（含 `task:*`）这类角色
        删掉或清空，而持有该角色的账号本身受保护——同一件事换个入口就绕过去了。
        """
        if role.is_superuser:
            self._require_superuser(actor, f"操作超级管理员角色 {role.code}")
            return
        perms = (await self.roles.permissions_for([role.id])).get(role.id, ())
        missing = self._missing_grants(actor, perms)
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
            # 这一步过去是人工的 `app sync-roles`：忘了跑就是「功能上线了，
            # 非超管点按钮 403，而超管自测全绿」。现在只在启动时补**新增**的
            # 权限点，管理员手工调过的配置不受影响（见 sync_new_permissions）。
            await service.sync_new_permissions()
        except SQLAlchemyError as exc:
            raise RuntimeError(
                "内置角色初始化失败：身份相关数据表可能尚未创建。"
                "生产环境请先执行 `alembic upgrade head`；开发环境检查 DB_URL。"
            ) from exc


__all__ = ["IdentityService", "ResolvedIdentity", "bootstrap"]
