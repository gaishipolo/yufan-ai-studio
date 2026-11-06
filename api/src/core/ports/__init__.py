"""跨模块注入口（port）：低层代码要问业务模块问题时的唯一通道。

为什么在 `core/` 而不是 `api/`：`api/` 是最上层（装配层），`modules/` 在它下面。
端口放在 `api/` 时，`modules` 能用、`generation` 不能用——一用就是反向依赖。
放在 `core/` 后，谁都能 import，而 core 依旧不认识任何业务模块。

已登记的端口见 `api/ports.py` 的 `PORT_CATALOG`（装配层知道所有层，所以目录放那儿）。
"""

from core.ports.accounts import (
    AccountStatusChecker,
    account_is_active,
    has_account_status_checker,
    register_account_status_checker,
    reset_account_status_checker,
)

__all__ = [
    "AccountStatusChecker",
    "account_is_active",
    "has_account_status_checker",
    "register_account_status_checker",
    "reset_account_status_checker",
]
