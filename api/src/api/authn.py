"""主体解析扩展点：把「令牌里声称的身份」换成「数据库里的权威身份」。

## 为什么需要这个注入点

`get_actor` 原来只做一件事：把 JWT 解成 `CurrentUser`。只有演示账号时这没问题，
接上真实账号后暴露出两个缺口，而且都不是"实现细节"级别：

1. **停用账号后令牌仍可用满 TTL**（默认 2 小时）。这是安全缺口，不是新鲜度问题：
   被停用的人照样能调生成接口，照样产生费用。
2. **改角色后必须重新登录才生效**，管理员撤销权限在窗口期内是无效的。

补法有两种。一种是直接在 `api/deps.py` 里 `from modules.identity.service import ...`，
但那样会：让装配层反向依赖业务模块、违反"新功能 = 新增文件"、并且踩上
`identity/dependencies.py -> api.deps` 的循环导入（`docs/EXTENDING.md` §2⑥ 记录过这个坑）。
一个扩展点需要使用者去改装配层，本身就说明抽象漏了。

另一种就是本文件：把「主体从哪来」变成与 `get_storage` / `get_queue` /
`get_model_router` **同形态的注入点**。身份模块实现 `PrincipalResolver` 并注册，
核心永远不认识它；换 SSO / LDAP / API Key 也只是注册另一个解析器。

## 兼容性

没有注册解析器时（删掉 identity 模块、或纯粹的令牌子系统），`resolve_principal`
原样返回令牌里的主体，行为与改造前**完全一致**。
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import RegistryError

__all__ = [
    "PrincipalResolver",
    "register_principal_resolver",
    "reset_principal_resolver",
    "resolve_principal",
]


class PrincipalResolver(Protocol):
    """令牌主体 → 权威主体。

    实现方拿到的 `principal` 只保证 `id` 可信（其余字段来自令牌，可能已过期）。
    返回值语义：

    - 返回 `CurrentUser`：本次请求以它为准（权限、角色、显示名全部覆盖）
    - 返回 `None`：主体已失效（账号被删/停用），调用方应回 401 而不是放行

    `session` 是本次请求的数据库会话，实现方复用它即可，不要另开连接。
    """

    async def resolve(
        self, principal: CurrentUser, session: AsyncSession
    ) -> CurrentUser | None: ...


_resolver: PrincipalResolver | None = None


def register_principal_resolver(resolver: PrincipalResolver) -> None:
    """注册主体解析器（由拥有账号体系的模块在 import 期调用）。

    只允许一个：两个解析器同时生效时"权限以谁为准"没有正确答案，
    与其猜，不如在启动期直接报错。
    """
    global _resolver
    if _resolver is not None and _resolver is not resolver:
        raise RegistryError(
            "主体解析器重复注册："
            f"{type(_resolver).__name__} 已生效，拒绝 {type(resolver).__name__}。"
            "账号体系只能有一个权威来源。"
        )
    _resolver = resolver


def reset_principal_resolver() -> None:
    """仅测试使用：清掉注册，验证「无解析器时行为不变」。"""
    global _resolver
    _resolver = None


def has_principal_resolver() -> bool:
    return _resolver is not None


async def resolve_principal(
    principal: CurrentUser, session: AsyncSession
) -> CurrentUser | None:
    """交给已注册的解析器；没有解析器时视为「令牌即权威」。"""
    if _resolver is None:
        return principal
    return await _resolver.resolve(principal, session)
