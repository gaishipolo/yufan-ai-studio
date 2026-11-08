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

# 模块启动钩子：应用 lifespan 内、worker 启动前调用。两种语义分得很清楚，别混用：
# - `startup`：**恢复**动作（把上次没跑完的任务接着跑），只在 `WORKER_ENABLED=true`
#   的实例上执行——只读 API 实例跑一遍会改写记录状态、把任务投进无人消费的队列。
# - `bootstrap`：**装配**动作（建内置角色、补权限点），与队列无关，任何实例都要执行。
StartupHook = Callable[[], Awaitable[None]]
# 模块演示数据钩子：`app seed` 时调用，返回写入行数。实现方自己 commit。
SeedHook = Callable[[AsyncSession], Awaitable[int]]


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    """一个可插拔的业务模块。

    `order` 决定路由注册顺序（小的先注册）。有通配路径的模块应调大该值，
    避免把别人的静态路径吃掉。

    `startup` / `bootstrap` 让模块自己处理「启动时要做什么」，这样 `app.py`
    不需要知道任何业务模块的存在。两者的分界线是**这个动作会不会改写业务状态**：

    - `startup` = 恢复。只在 `WORKER_ENABLED=true` 时执行：恢复未完成任务、把
      「提交结果未知」判死、发失败通知——只读 API 实例跑一遍纯属伤害（R13-01）。
    - `bootstrap` = 装配。**任何实例都执行**，必须是幂等的：建内置角色、把新增
      权限点补进角色、预热只读缓存这类「谁跑都一样、不跑就出错」的事。
      它曾经只能塞进 `startup`，代价是「没开 worker 的实例永远不会初始化内置
      角色，新增权限点也永远进不了老库」——那是写文档的人不会注意到的一种坏。
    """

    name: str
    router: APIRouter
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    order: int = 100
    description: str = ""
    permissions: tuple[PermissionSpec, ...] = ()
    startup: StartupHook | None = None
    # 装配型启动钩子：任何实例（含 `WORKER_ENABLED=false`）都会执行，必须幂等。
    # 与 `startup` 的差别见类文档：这边不许改写「正在进行的业务状态」。
    bootstrap: StartupHook | None = None
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
    # 本模块注册的 job handler 名（`workers.register_handler` 的 key）。
    # **声明是义务，不是注释**：`create_app()` 会核对「声明了就必须注册」，
    # 漏掉 `__init__.py` 里那句副作用 import 会直接起不来，而不是静默少一个 worker
    # （见 `api/assembly.py`）。反过来漏声明只会让 doctor 记一条 WARN。
    jobs: tuple[str, ...] = ()
    # 本模块注册的周期任务名。周期任务名本来就必须写成 `<模块>.<动作>`
    # （`core.scheduler.periodic` 的硬约束），这里再声明一次是为了让
    # 「漏 import」变成启动失败——名字前缀只能证明「谁命名得像」，证明不了「谁注册了」。
    periodic: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.tags[0] if self.tags else self.name

    def permission_codes(self) -> frozenset[str]:
        return frozenset(spec.code for spec in self.permissions)
