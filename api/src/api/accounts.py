"""账号状态扩展点：给「没有请求上下文」的调用方查「这个人还能不能干活」。

## 为什么需要它

`api/authn.py` 解决的是**请求内**的权威身份：每个带令牌的请求都会重新解析，
账号停用后立刻 401。但后台任务不走这条路径——worker 手里只有文件里的 `owner_id`，
既没有令牌也没有请求。于是出现一个缝：人已经被停用，队列里排着的活还在替他跑、
还在扣积分。停用只把人关在门外，没关掉已经在屋里干着的活。

## 为什么不直接 import identity

`tests/test_architecture.py::test_modules_do_not_import_each_other` 明令禁止业务模块
互相 import，理由很实在：这种依赖一旦开一个口子，模块就没法单独删、单独换。
所以这里和 `get_storage` / `get_queue` / `PrincipalResolver` 一样，做成**注入点**：
身份模块实现并注册，消费方（后台任务、CLI）只认这个协议，核心永远不认识 identity。

## 语义

`account_is_active()` 返回三态，不要压成 bool：

- `True`  —— 账号存在且启用
- `False` —— 账号存在但已停用（该停手）
- `None`  —— 查不到（没有注册实现、账号已物理删除、历史档案里的合成 owner）

`None` 必须按「放行」处理。这个口子的目的是**关掉离职后继续花钱**，
不是给所有历史数据补一道会误伤的闸门——把 `None` 当停用，会让所有老档案卡死。
"""

from __future__ import annotations

from typing import Protocol

from core.exceptions import RegistryError

__all__ = [
    "AccountStatusChecker",
    "account_is_active",
    "has_account_status_checker",
    "register_account_status_checker",
    "reset_account_status_checker",
]


class AccountStatusChecker(Protocol):
    """账号状态查询。实现方自己开会话，调用方不传 session（这里没有请求）。"""

    async def is_active(self, user_id: str) -> bool | None: ...


_checker: AccountStatusChecker | None = None


def register_account_status_checker(checker: AccountStatusChecker) -> None:
    """注册实现（由拥有账号体系的模块在 import 期调用）。

    只允许一个：两个账号来源同时生效时「谁说了算」没有正确答案，
    与其猜，不如在启动期直接报错——和 `register_principal_resolver` 同一个取舍。
    """
    global _checker
    if _checker is not None and _checker is not checker:
        raise RegistryError(
            "账号状态查询器重复注册："
            f"{type(_checker).__name__} 已生效，拒绝 {type(checker).__name__}。"
            "账号体系只能有一个权威来源。"
        )
    _checker = checker


def reset_account_status_checker() -> None:
    """仅测试使用：清掉注册，验证「没有实现时按放行处理」。"""
    global _checker
    _checker = None


def has_account_status_checker() -> bool:
    return _checker is not None


async def account_is_active(user_id: str) -> bool | None:
    """查账号是否启用；没有注册实现时返回 None（调用方按放行处理）。"""
    if _checker is None:
        return None
    return await _checker.is_active(user_id)
