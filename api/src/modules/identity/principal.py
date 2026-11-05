"""把数据库里的账号状态接进请求链路（`api/authn.py` 的解析器实现）。

注册之后，`get_actor` 不再相信令牌里的 `perms`，只看 `sub`：

| 场景 | 改造前 | 改造后 |
|---|---|---|
| 管理员撤了某人的权限 | 最晚等 2 小时（TTL）才生效 | 下一个请求就生效 |
| 账号被停用 | 令牌仍可用满 2 小时 | 立刻 401 |
| 用户改了昵称 | 仍是旧昵称 | 立即同步（日志、任务作者快照除外） |

代价是每个带令牌的请求多 2~3 次单表查询（用户 / 角色 / 权限）。
这个量级在后台系统里完全可接受；真到瓶颈再加请求级或秒级缓存，
而不是为了省这几次查询把权限新鲜度换掉。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.context import CurrentUser
from modules.identity.service import IdentityService

if TYPE_CHECKING:  # 仅用于类型标注，避免运行期多引入
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["IdentityPrincipalResolver"]


class IdentityPrincipalResolver:
    """令牌主体 → 数据库主体。

    `resolve` 只做两件事：确认账号还存在且启用、把角色展开成权限码。
    任何业务判断（能不能进这个接口）仍然交给 `require()`，这里不越界。
    """

    async def resolve(
        self, principal: CurrentUser, session: AsyncSession
    ) -> CurrentUser | None:
        resolved = await IdentityService(session).resolve_identity(principal.id)
        if resolved is None or not resolved.user.is_active:
            return None

        user = resolved.user
        return CurrentUser(
            id=user.id,
            name=user.display_name or user.username,
            roles=resolved.role_codes,
            permissions=resolved.permissions,
        )
