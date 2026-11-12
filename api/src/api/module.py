"""模块描述对象。

单独成文件是为了避免 `registry ←→ modules` 的循环导入：
registry 与各业务模块都从这里取 `ModuleSpec`。

`ModuleSpec` 是一个模块对外暴露的**唯一契约**：路由、权限点、说明。
自动发现只认它，不认目录结构，所以模块内部怎么组织都行。
"""

from __future__ import annotations

import argparse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from core.permissions import PermissionSpec
from core.ports.assets import AssetReferenceSource
from core.ports.library_references import LibraryReferenceSource

# 自检结论的档位。定义在这里而不是 `api/diagnostics.py`：模块要按它填报告，
# 而模块不该为了一个类型去 import 诊断实现。诊断层反过来从这里取（单向）。
Level = Literal["PASS", "INFO", "WARN", "FAIL"]

# 模块启动钩子：应用 lifespan 内、worker 启动前调用。两种语义分得很清楚，别混用：
# - `startup`：**恢复**动作（把上次没跑完的任务接着跑），只在 `WORKER_ENABLED=true`
#   的实例上执行——只读 API 实例跑一遍会改写记录状态、把任务投进无人消费的队列。
# - `bootstrap`：**装配**动作（建内置角色、补权限点），与队列无关，任何实例都要执行。
StartupHook = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SeedContext:
    """演示数据装载时传给每个模块的共享上下文。

    为什么需要它：演示数据是**跨模块连贯**的（任务属于某个用户、分镜引用模板），
    拆到各模块之后，「那个用户是谁」必须有个共同的答案。可选的两条路都不好：

    - 各模块硬编码同一个常量（`"u_001"` 抄五遍）——改一次要改五处，且没有任何
      地方会告诉你改漏了；
    - 让模块之间互相 import 那个常量——直接违反「模块之间不互相 import」。

    所以框架只认识**一个字符串**：演示账号的 id。它不知道用户表长什么样，
    也不知道谁在用它，只负责在种子装载开始时把它算出来、发下去。
    """

    demo_user_id: str
    demo_user_name: str


# 模块演示数据钩子：`app seed` 时调用，返回写入行数。实现方自己 commit。
SeedHook = Callable[[AsyncSession, SeedContext], Awaitable[int]]

# 模块命令行注册钩子：`app <command>` 的解析器组装阶段调用，模块把自己的
# 运维子命令挂上去（`sub.add_parser(...)` + `set_defaults(func=...)`）。
# 没有它的时候，每条业务运维命令都写在 `src/cli.py` 里，于是那个 1200 行的文件
# 必须 `from modules.identity.service import ...`——加一条命令要动框架，
# 删一个模块会让备份/恢复这些救命命令一起 import 失败。
#
# 写成 Protocol 而不是 `Callable[[...], None]`：`argparse._SubParsersAction`
# 在运行期不可下标（`from __future__ import annotations` 只救注解，救不了
# 模块级的类型别名赋值），而函数体里的注解是延迟求值的，所以这样写既能让 mypy
# 拿到真实类型，又不会在 import 期炸。
class CLIRegistrar(Protocol):
    def __call__(self, sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None: ...


class DoctorReportLike(Protocol):
    """`app doctor` 的报告对象（真实类型是 `api.diagnostics.DoctorReport`）。

    这里只声明模块用得到的那一个方法：模块自检**只能往报告里加一条**，
    不该改动别人的结论、也不该读整份报告去做判断——那会让「哪条检查归谁」
    重新变成一团。用 Protocol 而不是 import 真实类型，是为了让
    `api/module.py` 不认识诊断实现（诊断随时可以换掉）。
    """

    def add(self, name: str, level: Level, message: str) -> None: ...


# 模块自检钩子：`app doctor` 末尾逐个调用。
# 为什么需要它：doctor 里有几条检查天生只对一个模块成立（「生图结果还是旧
# `/static` 地址吗」「归档用量离配额还有多远」）。这些检查直接写在
# `api/diagnostics.py` 里，就等于**框架为了几条业务检查反向 import 业务模块**——
# 删掉那个模块，doctor 直接崩；而手册第 0 节写着 api 不认识业务模块。
DoctorHook = Callable[[DoctorReportLike], Awaitable[None]]


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
    # 演示数据的装载顺序（小的先装）。
    # **刻意与 `order` 分开**：`order` 决定路由注册顺序，动它会改契约里 tag/path
    # 的先后；而「谁先种数据」是另一件事——identity 必须先种，否则其它模块的
    # 演示行指向一个不存在的用户。两件事共用一个字段，调接口顺序就会顺手弄坏演示数据。
    seed_order: int = 100
    # 模块的运维子命令：`app <command>` 组装解析器时逐个调用。
    # 没有它的时候，建号、清审计、归档迁移这些命令全写在 `src/cli.py`，
    # 而那个文件反过来 import 业务模块——加命令要改框架，删模块会连累 `app backup`。
    cli: CLIRegistrar | None = None
    # 公开模块：健康检查、探活等无需登录/权限的接口。
    # 默认 False —— 新模块默认受权限保护，避免「忘了加权限点」变成裸奔。
    public: bool = False
    # 本模块把 `assets.id` 写进了哪些持久化位置。**漏声明 = 删除会被 fail-closed
    # 挡住（503），CI 的 AST 守卫也会红**：素材彻底删除不可逆，不能靠「记得注册」。
    # 声明与实现是同一份常量：模块 import 期调 `register_asset_reference_sources()`
    # 交给注入口，框架用这里的声明做完备性对账（`core/ports/assets.py`）。
    asset_reference_sources: tuple[AssetReferenceSource, ...] = ()
    # 本模块把 `library_assets.id`（v2 资产）写进了哪些持久化位置。
    # 与上面的 `asset_reference_sources` **不能合并**：那是 v1 `assets.id` 的引用
    # （`video_tasks`、模板库老引用），这是 v2 的 id 空间，两套 id 混查会把
    # 「A 引用了同名 id」当成「B 被引用」——两边都是不可逆删除前的最后一道闸。
    # **漏声明 = 删除会被 fail-closed 挡住（503），CI 的 AST 守卫也会红**。
    library_reference_sources: tuple[LibraryReferenceSource, ...] = ()
    # 本模块注册的 job handler 名（`workers.register_handler` 的 key）。
    # **声明是义务，不是注释**：`create_app()` 会核对「声明了就必须注册」，
    # 漏掉 `__init__.py` 里那句副作用 import 会直接起不来，而不是静默少一个 worker
    # （见 `api/assembly.py`）。反过来漏声明只会让 doctor 记一条 WARN。
    jobs: tuple[str, ...] = ()
    # 本模块注册的周期任务名。周期任务名本来就必须写成 `<模块>.<动作>`
    # （`core.scheduler.periodic` 的硬约束），这里再声明一次是为了让
    # 「漏 import」变成启动失败——名字前缀只能证明「谁命名得像」，证明不了「谁注册了」。
    periodic: tuple[str, ...] = ()
    # 模块自检：`app doctor` 逐个调用，往报告里加自己那几条。
    # 没有它时，凡是「只对这个模块成立」的检查都写在 `api/diagnostics.py` 里，
    # 于是框架反向 import 业务模块——删模块 = doctor 崩，加模块 = 改框架。
    doctor: DoctorHook | None = None

    @property
    def label(self) -> str:
        return self.tags[0] if self.tags else self.name

    def permission_codes(self) -> frozenset[str]:
        return frozenset(spec.code for spec in self.permissions)
