"""把数据库里的账号状态接进请求链路（`api/authn.py` 的解析器实现）。

注册之后，`get_actor` 不再相信令牌里的 `perms`，只看 `sub`：

| 场景 | 改造前 | 改造后 |
|---|---|---|
| 管理员撤了某人的权限 | 最晚等 2 小时（TTL）才生效 | 下一个请求就生效 |
| 账号被停用 | 令牌仍可用满 2 小时 | 立刻 401 |
| 用户点了「退出登录」 | 只是前端删了本地副本 | 立刻 401（会话已吊销） |
| 用户改了昵称 | 仍是旧昵称 | 立即同步（日志、任务作者快照除外） |

代价是每个带令牌的请求多 2~4 次单表查询（用户 / 角色 / 权限 / 会话）。
这个量级在后台系统里完全可接受；真到瓶颈再加请求级或秒级缓存，
而不是为了省这几次查询把权限新鲜度换掉。
"""

from __future__ import annotations

from datetime import UTC
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

    额外一条：**改密之前的令牌一律作废**。停用和改权限都能即时生效，
    唯独改密码不生效的话，「账号被盗 → 改密码」这个最标准的自救动作等于没做。
    判定用令牌自带的 `iat` 与库里的 `password_changed_at` 比，不需要黑名单。
    """

    async def resolve(
        self, principal: CurrentUser, session: AsyncSession
    ) -> CurrentUser | None:
        identity_service = IdentityService(session)
        resolved = await identity_service.resolve_identity(principal.id)
        if resolved is None or not resolved.user.is_active:
            return None

        user = resolved.user
        if principal.issued_at and user.password_changed_at is not None:
            changed_at = user.password_changed_at
            if changed_at.tzinfo is None:  # sqlite 读回来是 naive，按 UTC 解释
                changed_at = changed_at.replace(tzinfo=UTC)
            if principal.issued_at < int(changed_at.timestamp()):
                return None

        # 会话校验：登出 / 踢下线 / 会话过期都在这里生效。
        #
        # 为什么必须有这一步：令牌是无状态的，「签名有效」只说明它没被篡改，
        # 不说明它还被允许使用。没有这一步，登出就只是前端删了本地副本，
        # 复制走的那张还能用满 TTL。
        #
        # `session_id` 为空表示这张令牌不是登录签出来的（旧令牌、测试用令牌、
        # 匿名演示主体），此时跳过——它们没有会话可查，靠上面的改密判定和 TTL 兜底。
        if principal.session_id:
            active = await identity_service.sessions.active_by_id(
                principal.session_id, user.id
            )
            if active is None:
                return None

        return CurrentUser(
            id=user.id,
            name=user.display_name or user.username,
            roles=resolved.role_codes,
            permissions=resolved.permissions,
            issued_at=principal.issued_at,
            session_id=principal.session_id,
        )
