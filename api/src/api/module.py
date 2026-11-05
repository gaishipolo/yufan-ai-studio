"""模块描述对象。

单独成文件是为了避免 `registry ←→ modules` 的循环导入：
registry 与各业务模块都从这里取 `ModuleSpec`。

`ModuleSpec` 是一个模块对外暴露的**唯一契约**：路由、权限点、说明。
自动发现只认它，不认目录结构，所以模块内部怎么组织都行。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastapi import APIRouter

from core.permissions import PermissionSpec

# 模块启动钩子：应用 lifespan 内、worker 启动前调用
StartupHook = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    """一个可插拔的业务模块。

    `order` 决定路由注册顺序（小的先注册）。有通配路径的模块应调大该值，
    避免把别人的静态路径吃掉。

    `startup` 让模块自己处理「启动时要做什么」（恢复未完成任务、预热缓存…），
    这样 `app.py` 不需要知道任何业务模块的存在。
    """

    name: str
    router: APIRouter
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    order: int = 100
    description: str = ""
    permissions: tuple[PermissionSpec, ...] = ()
    jobs: tuple[str, ...] = ()
    startup: StartupHook | None = None
    # 公开模块：健康检查、探活等无需登录/权限的接口。
    # 默认 False —— 新模块默认受权限保护，避免「忘了加权限点」变成裸奔。
    public: bool = False

    @property
    def label(self) -> str:
        return self.tags[0] if self.tags else self.name

    def permission_codes(self) -> frozenset[str]:
        return frozenset(spec.code for spec in self.permissions)
