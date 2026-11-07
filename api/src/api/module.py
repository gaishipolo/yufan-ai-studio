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
from sqlalchemy.ext.asyncio import AsyncSession

from core.permissions import PermissionSpec
from core.ports.assets import AssetReferenceSource

# 模块启动钩子：应用 lifespan 内、worker 启动前调用；**仅在 `WORKER_ENABLED=true` 时执行**
StartupHook = Callable[[], Awaitable[None]]
# 模块演示数据钩子：`app seed` 时调用，返回写入行数。实现方自己 commit。
SeedHook = Callable[[AsyncSession], Awaitable[int]]


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    """一个可插拔的业务模块。

    `order` 决定路由注册顺序（小的先注册）。有通配路径的模块应调大该值，
    避免把别人的静态路径吃掉。

    `startup` 让模块自己处理「启动时要做什么」（恢复未完成任务、预热缓存…），
    这样 `app.py` 不需要知道任何业务模块的存在。

    **语义边界**：它在 `WORKER_ENABLED=true` 时才会被调用——恢复未完成任务、
    预热只属于消费队列的那个实例，只读 API 实例跑一遍会改写记录状态并把任务
    投进无人消费的队列（R13-01）。所以它只该放「本实例消费队列才该发生」的动作；
    与队列无关的启动动作（例如只读实例也要做的缓存预热）不该塞在这里，
    否则会被一起跳过。
    """

    name: str
    router: APIRouter
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    order: int = 100
    description: str = ""
    permissions: tuple[PermissionSpec, ...] = ()
    startup: StartupHook | None = None
    # 本模块负责的生成 kind（`generation.pipeline` 的注册表键）。
    # 没有 `startup` 的模块由 `generation/recovery.py` 按这个声明统一恢复；
    # 有 `startup` 的模块沿用自己的恢复器，框架不插手。
    # 这个字段是「模块 × 插槽」矩阵的机械守卫依据：漏声明 = CI 红（R11-01）。
    recovery_kinds: tuple[str, ...] = ()
    # 模块自己的演示数据。没有它的时候，每加一个模块都要去改 `src/seed.py`，
    # 而那个文件会越来越像一个「所有模块的 union」——新模块的演示数据写着写着就变成框架债。
    seed: SeedHook | None = None
    # 公开模块：健康检查、探活等无需登录/权限的接口。
    # 默认 False —— 新模块默认受权限保护，避免「忘了加权限点」变成裸奔。
    public: bool = False
    # 本模块把 `assets.id` 写进了哪些持久化位置。**漏声明 = 删除会被 fail-closed
    # 挡住（503），CI 的 AST 守卫也会红**：素材彻底删除不可逆，不能靠「记得注册」。
    # 声明与实现是同一份常量：模块 import 期调 `register_asset_reference_sources()`
    # 交给注入口，框架用这里的声明做完备性对账（`core/ports/assets.py`）。
    asset_reference_sources: tuple[AssetReferenceSource, ...] = ()

    @property
    def label(self) -> str:
        return self.tags[0] if self.tags else self.name

    def permission_codes(self) -> frozenset[str]:
        return frozenset(spec.code for spec in self.permissions)
