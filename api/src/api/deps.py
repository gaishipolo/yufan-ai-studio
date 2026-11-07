"""通用依赖：数据库会话、分页、当前操作者、基础设施、权限。

各业务模块的 service 依赖在自己的 `dependencies.py` 里组装，
这里只提供与业务无关的横切依赖。

权限用法（不需要改本文件）：

    from api.deps import require

    @router.post("/voices")
    async def create_voice(
        payload: CreateVoiceIn,
        actor: CurrentUser = Depends(require("voice:create")),
    ): ...
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from api.authn import resolve_principal
from api.ratelimit_policy import mark_rate_limited
from core.config import settings
from core.context import CurrentUser, set_current_user
from core.exceptions import Forbidden, RateLimited, Unauthorized
from core.net import client_ip
from core.ratelimit import get_rate_limiter, retry_after_seconds
from core.response import PageParams
from core.security import user_from_token
from db.session import get_session
from infra import get_image_provider, get_queue, get_storage, get_video_provider
from infra.ai.base import ModelProvider
from infra.queue.base import JobQueue
from infra.storage.base import ObjectStorage

# ---------- 数据 ----------
SessionDep = Annotated[AsyncSession, Depends(get_session)]
PageDep = Annotated[PageParams, Depends()]

# ---------- 基础设施（面向协议注入）----------
StorageDep = Annotated[ObjectStorage, Depends(get_storage)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]
VideoProviderDep = Annotated[ModelProvider, Depends(get_video_provider)]
ImageProviderDep = Annotated[ModelProvider, Depends(get_image_provider)]

# ---------- 身份 ----------
# 演示账号持有超级权限，本地联调不需要造角色数据
DEMO_USER = CurrentUser(
    id="u_001", name="老大", roles=("admin",), permissions=frozenset({"*"})
)

# 用 HTTPBearer 而不是裸 Header 解析：FastAPI 会据此在 OpenAPI 里生成
# `securitySchemes`，并**自动**给所有依赖 get_actor 的接口标上「需要令牌」。
# login / health 不依赖 get_actor，因此不会被误标 —— 不需要维护白名单。
# auto_error=False：缺令牌时由本函数决定（非生产环境回退演示账号，生产回 401）。
BEARER_SCHEME = HTTPBearer(
    auto_error=False,
    scheme_name="bearerAuth",
    description="登录接口返回的 accessToken，请求头填 `Authorization: Bearer <token>`",
)


async def get_actor(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER_SCHEME)] = None,
) -> CurrentUser:
    """解析当前操作者。

    令牌只提供**身份**（`sub`），权限由 `resolve_principal` 决定：
    身份模块注册了解析器就以数据库为准（停用即失效、改角色立即生效），
    没有解析器时退化为「令牌里的权限即权威」（与改造前行为一致）。

    未登录时仅非生产环境回退到演示账号——演示账号不走解析器，
    它是本地联调用的合成主体，不该受真实数据影响（否则停用 u_001 会让本地开发登不进去）。
    """
    if credentials is not None:
        claimed = user_from_token(credentials.credentials)
        user = await resolve_principal(claimed, session)
        if user is None:
            # 令牌本身没坏，坏的是它指向的账号（停用 / 被删 / 改过密码）：
            # 必须回 401 让前端重新登录
            raise Unauthorized("登录状态已失效，请重新登录")
        set_current_user(user)
        request.state.user = user
        return user

    if settings.anonymous_allowed:
        set_current_user(DEMO_USER)
        request.state.user = DEMO_USER
        return DEMO_USER

    raise Unauthorized("请先登录")


ActorDep = Annotated[CurrentUser, Depends(get_actor)]


# ---------- 权限 ----------
def require(
    *codes: str, mode: Literal["all", "any"] = "all"
) -> Callable[..., Awaitable[CurrentUser]]:
    """权限依赖工厂。

        actor: CurrentUser = Depends(require("task:create"))
        actor: CurrentUser = Depends(require("billing:read", "billing:write", mode="any"))

    权限码由模块自己在 `ModuleSpec.permissions` 声明，使用前无需在核心登记。
    """
    if not codes:
        raise ValueError("require() 至少需要一个权限码")

    async def dependency(actor: ActorDep) -> CurrentUser:
        granted = actor.has_all(codes) if mode == "all" else actor.has_any(codes)
        if not granted:
            raise Forbidden(f"缺少权限: {' + '.join(codes)}")
        return actor

    dependency.__name__ = f"require_{'_'.join(c.replace(':', '_') for c in codes)}"
    return dependency


# ---------- 限流 ----------
def rate_limit(
    scope: str,
    *,
    limit: int,
    window: float = 60.0,
    cost: int = 1,
) -> Callable[..., Awaitable[None]]:
    """限流依赖工厂。与 `require()` 同一类东西：横切在路由上的「闸门」。

        @router.post("")
        async def create(..., _: None = Depends(rate_limit("task.create", limit=30))): ...

    - `scope` 是**动作**（`task.create`），`limit` 是窗口内允许的次数，`cost`
      让「一次请求算几次」可配（上传大文件算 5 次是常见做法）。
    - 计数主体是**当前登录用户**；未登录走不到这里（依赖了 `ActorDep`），
      因为本项目所有业务接口都要求身份。纯匿名接口要按 IP 限流时，
      应该写 `Depends(rate_limit_by_ip(...))` 那种变体，不要放松这里的语义。
    - 超出额度抛 429（`RateLimited`），`details.retryAfter` 是建议重试秒数，
      前端据此倒计时；**不设 `Retry-After` 头**，因为响应封套才是本项目的契约。
    """
    if not scope:
        raise ValueError("rate_limit() 需要 scope")
    if limit <= 0:
        # 显式关闭：返回一个空依赖，比在路由里写 `if settings.xxx` 干净。
        # 仍然打标记：额度 0 是「声明过的决定」，不是「忘了挂」——
        # `api/ratelimit_policy.py` 的守卫只看标记在不在。
        async def _noop() -> None:
            return None

        return mark_rate_limited(_noop, scope)

    async def dependency(request: Request, actor: ActorDep) -> None:
        if not settings.rate_limit_enabled:
            return
        decision = await get_rate_limiter().hit(
            actor.id or client_ip(request),
            scope=scope,
            limit=limit,
            window=window,
            cost=cost,
        )
        if not decision.allowed:
            raise RateLimited(
                f"操作过于频繁，请 {retry_after_seconds(decision)} 秒后再试",
                details={
                    "scope": scope,
                    "limit": decision.limit,
                    "retryAfter": retry_after_seconds(decision),
                },
            )

    dependency.__name__ = f"rate_limit_{scope.replace(':', '_').replace('.', '_')}"
    # 标记给 `api/ratelimit_policy.py` 的守卫读：写路由要么挂了这个依赖，
    # 要么在豁免表里写理由，两者都没有就是漏挂。
    return mark_rate_limited(dependency, scope)


# 这里曾经有 `require_role()` / `AdminDep`：按角色名判定。
# 身份模块把角色展开成权限码之后，它们既没有调用方也没有测试，
# 留着只会让人以为「写死角色名」是允许的（R4-08）。
