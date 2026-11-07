"""请求级上下文：用 contextvars 贯穿一次调用链。

日志、审计、下游调用都可以直接取到 request_id / current_user，无需层层传参。
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

SUPER_PERMISSION = "*"


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """轻量主体对象。

    `permissions` 是权限码集合，支持 `task:*` 与 `*` 两种通配。
    角色（role）只作为兼容层保留：生产应改为「角色 → 权限集」在登录时展开，
    这样业务代码永远只判断权限码，不再判断角色名。
    """

    id: str
    name: str
    roles: tuple[str, ...] = ()
    permissions: frozenset[str] = frozenset()
    # 令牌签发时间（epoch 秒）。0 表示「不知道」——合成主体（匿名演示账号、
    # 单元测试里的 CurrentUser）都取 0，解析器据此跳过「令牌是否早于改密」的判断。
    issued_at: int = 0
    # 对应的会话 id（access 令牌里的 `sid`）。空字符串表示「这张令牌没有会话」
    # ——旧令牌、合成主体、测试里直接签的令牌都是这样，解析器据此跳过会话校验。
    session_id: str = ""

    def has(self, code: str) -> bool:
        """是否拥有某权限码。"""
        if SUPER_PERMISSION in self.permissions:
            return True
        if code in self.permissions:
            return True
        resource, _, _ = code.partition(":")
        return f"{resource}:*" in self.permissions

    def has_all(self, codes: tuple[str, ...] | list[str]) -> bool:
        return all(self.has(code) for code in codes)

    def has_any(self, codes: tuple[str, ...] | list[str]) -> bool:
        return any(self.has(code) for code in codes)


_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_current_user: ContextVar[CurrentUser | None] = ContextVar("current_user", default=None)


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(value: str) -> None:
    _request_id.set(value)


def get_current_user() -> CurrentUser | None:
    return _current_user.get()


def set_current_user(user: CurrentUser | None) -> None:
    _current_user.set(user)


def reset_request_context() -> None:
    """把请求级 contextvar 复位到「新请求」的状态。

    必须在每个请求**开始时**调用，不能只在设置的地方覆盖：

    - `current_user` 只在**认证成功**的分支里被写入。一个没带令牌的请求
      （`/metrics`、404、静态资源）不会写它，于是它会读到上一个请求留下的用户；
    - contextvar 是顺着任务/连接上下文传的。每个请求一个 task 的服务器不会串，
      但在同一个 task 里连续处理请求的实现（嵌入式、测试客户端、部分网关）会串。
      症状是「审计/日志里这个请求显示成了上一个登录用户」，只在特定连接形态下
      偶发，几乎没法靠回放复现。

    复位而不是 `ContextVar.reset(token)`：请求的入口不止一个（middleware 之外的
    手工调用、CLI、测试），把「当前值」显式设成初值不需要谁持有 token。
    """
    _request_id.set("-")
    _current_user.set(None)
