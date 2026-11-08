"""装配对账：模块声明了什么 vs 运行时真的注册了什么。

## 为什么需要这个文件

这套框架的装配主要靠**副作用注册**：`modules/x/__init__.py` 里多写一行
`from modules.x import worker`，handler 才注册；漏写，功能就静默不工作。
启动、doctor、契约生成、既有测试**全都不报错**——症状是「这个功能偶尔不生效」，
而病灶在一个没人会去看的 import 列表里。

`ModuleSpec.recovery_kinds` 已经用「声明 + CI 守卫」解决过一次同类问题
（注释原文：「漏声明 = CI 红」）。本文件把同一套办法推广到另外两类装配物：

- **job handler**（`workers.register_handler`）
- **周期任务**（`core.scheduler.periodic`）

## 两条方向，两种后果

1. **声明了但没注册** → 启动失败。
   这是漏副作用 import 的唯一可靠信号。必须 fail fast：一个声明了
   `export.build` 却没有 handler 的进程，会把任务永远堵在队列里。

2. **注册了但没声明** → 只在 doctor 里 WARN。
   它意味着清单不全（下一个人看不到这个 handler 属于谁），不影响运行。
   不阻断启动是因为**误报代价不对称**：测试里局部重置注册表很常见，
   为此让服务起不来，比漏一条声明更糟。

框架目录（`generation/`、`db/`）贡献的装配物没有 `ModuleSpec`，用下面的常量
显式登记。新增一条要改这里——这是刻意的：框架自己加 handler 是罕见事件，
模块漏声明是常见事件，两种代价不该一样。
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from api.module import ModuleSpec
from core.exceptions import RegistryError

__all__ = [
    "FRAMEWORK_JOBS",
    "FRAMEWORK_PERIODIC",
    "AssemblyGap",
    "assembly_gaps",
    "assert_assembly",
    "permission_gaps",
    "permission_gaps_for_modules",
]

#: 由框架目录（不是 `modules/*`）注册的 job handler
FRAMEWORK_JOBS: frozenset[str] = frozenset({"generation.poll", "generation.submit"})

#: 由框架目录注册的周期任务
FRAMEWORK_PERIODIC: frozenset[str] = frozenset(
    {"core.backup", "core.outbox", "core.outbox.prune"}
)


@dataclass(frozen=True, slots=True)
class AssemblyGap:
    """一处对不上的装配物。"""

    kind: str  # "job" | "periodic"
    name: str
    owner: str  # 模块名；框架装配物为空串
    problem: str  # "declared-but-missing" | "registered-but-undeclared"

    @property
    def fatal(self) -> bool:
        return self.problem == "declared-but-missing"

    def __str__(self) -> str:
        if self.problem == "declared-but-missing":
            return (
                f"{self.owner} 在 ModuleSpec 里声明了 {self.kind} {self.name!r}，"
                f"但运行时没有注册——多半漏了 __init__.py 里的副作用 import"
            )
        owner = f"（{self.owner}）" if self.owner else ""
        return (
            f"{self.kind} {self.name!r} 已注册{owner}，但没有模块在 ModuleSpec 里声明它，"
            f"`app modules` 看不到它属于谁"
        )


def _module_owner(dotted: str) -> str:
    """`modules.export.worker` -> `export`；框架目录返回空串。"""
    parts = dotted.split(".")
    if len(parts) >= 2 and parts[0] == "modules":
        return parts[1]
    return ""


def assembly_gaps(
    specs: Iterable[ModuleSpec],
    *,
    jobs: Mapping[str, str],
    periodic: Mapping[str, str],
) -> list[AssemblyGap]:
    """对账。`jobs` / `periodic` 的值是该装配物所在模块的点分路径。

    传入 `{name: fn.__module__}` 即可，见 `app.collect_assembly()`。
    """
    specs = list(specs)
    gaps: list[AssemblyGap] = []

    for kind, declared_attr, registry, framework in (
        ("job", "jobs", jobs, FRAMEWORK_JOBS),
        ("periodic", "periodic", periodic, FRAMEWORK_PERIODIC),
    ):
        declared: dict[str, str] = {}
        for spec in specs:
            for name in getattr(spec, declared_attr):
                declared[name] = spec.name
                if name not in registry:
                    gaps.append(
                        AssemblyGap(kind, name, spec.name, "declared-but-missing")
                    )
        for name in registry:
            if name in declared or name in framework:
                continue
            gaps.append(
                AssemblyGap(
                    kind,
                    name,
                    _module_owner(registry[name]),
                    "registered-but-undeclared",
                )
            )

    return gaps


def assert_assembly(
    specs: Iterable[ModuleSpec],
    *,
    jobs: Mapping[str, str],
    periodic: Mapping[str, str],
) -> None:
    """启动期调用：声明未兑现就起不来。

    只拦 `declared-but-missing`——「注册了没声明」交给 `app doctor` 的 WARN，
    它不影响运行，却可能在测试里因为局部重置注册表而误报。
    """
    fatal = [gap for gap in assembly_gaps(specs, jobs=jobs, periodic=periodic) if gap.fatal]
    if not fatal:
        return
    raise RegistryError(
        "模块装配不完整：\n  " + "\n  ".join(str(gap) for gap in fatal)
    )


def permission_gaps(routes: Iterable[object], known: Collection[str]) -> list[str]:
    """路由实际引用的权限码，必须由某个模块的 `ModuleSpec` 声明过。

    为什么要在启动期查：`require("export:readx")` 拼错一个字母时，超级管理员
    （权限 `*`）一切正常，其他人永远 403。启动、doctor、契约生成、既有测试
    全都不报错——这种「只有一部分人遇到」的问题最难查。

    与 `tests/test_architecture.py` 里的 AST 守卫是**互补**关系，不是重复：
    - AST 守卫扫源码字面量，能在 CI 阶段就红，但扫不到从常量/变量拼出来的码；
    - 这里读运行时依赖对象上的标记，覆盖后者，代价是要等应用装配完。

    依赖对象上的标记由 `api/deps.py::require` 打（`required_permissions`）。
    """
    missing: dict[str, set[str]] = {}
    for route in routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        pending = [dependant]
        while pending:
            dep = pending.pop()
            codes = getattr(getattr(dep, "call", None), "required_permissions", ())
            for code in codes:
                if code not in known:
                    missing.setdefault(str(code), set()).add(str(getattr(route, "path", "?")))
            pending.extend(getattr(dep, "dependencies", ()) or ())

    return [
        f"{code}（用于 {', '.join(sorted(paths)[:3])}）"
        for code, paths in sorted(missing.items())
    ]


def permission_gaps_for_modules(
    specs: Iterable[ModuleSpec], known: Collection[str]
) -> list[str]:
    """按模块遍历真实路由收集权限码缺口。

    不能遍历 `build_v1_router()` 的产物：当前 FastAPI 版本里
    `include_router` 放的是 `_IncludedRouter` 占位（延迟到建 OpenAPI 才展开），
    它的 `.routes` 数量等于**模块数**而不是接口数，照着遍历会得到「零个问题」——
    一个看起来生效、实际上永远说 OK 的检查。
    """
    return [
        gap for spec in specs for gap in permission_gaps(spec.router.routes, known)
    ]
