"""`AccountStatusChecker` 的身份模块实现。

只做一件事：用**自己的会话**查一次权威身份，回答「这个账号现在还能不能用」。
之所以不复用请求会话，是因为唯一的调用方是后台任务和 CLI——那里根本没有请求。
"""

from __future__ import annotations

from core.ports.accounts import AccountStatusChecker

__all__ = ["IdentityAccountStatus"]


class IdentityAccountStatus(AccountStatusChecker):
    async def is_active(self, user_id: str) -> bool | None:
        # 局部 import：本模块在 `modules.identity.__init__` 里注册，导入期越少越好；
        # 顺带避开 `service → repository → ...` 在包初始化过程中被提前拉起的循环。
        from db.session import get_session_factory
        from modules.identity.service import IdentityService

        async with get_session_factory()() as session:
            identity = await IdentityService(session).resolve_identity(user_id)
        if identity is None:
            # 账号不存在（物理删除、历史档案里的合成 owner）→ 交给调用方按放行处理
            return None
        return identity.user.is_active
