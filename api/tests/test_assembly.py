"""装配对账：模块声明 vs 运行时注册。

这组用例锁的是「漏一行副作用 import 会怎样」。在 `api/assembly.py` 之前，
答案是「什么都不发生」——启动、doctor、契约、既有测试全绿，只是那个功能
永远不生效。所以每一条都对着一个具体的失败方向写。
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute

from api.assembly import (
    FRAMEWORK_JOBS,
    FRAMEWORK_PERIODIC,
    assembly_gaps,
    assert_assembly,
)
from api.deps import require
from api.discovery import discover_modules
from api.module import ModuleSpec
from core.exceptions import RegistryError


def _spec(
    name: str, *, jobs: tuple[str, ...] = (), periodic: tuple[str, ...] = ()
) -> ModuleSpec:
    return ModuleSpec(name=name, router=APIRouter(), jobs=jobs, periodic=periodic)


def test_declared_but_missing_is_fatal() -> None:
    """声明了 job 却没人注册 —— 漏副作用 import 的唯一信号，必须阻断启动。"""
    gaps = assembly_gaps(
        [_spec("export", jobs=("export.build",))],
        jobs={},
        periodic={},
    )

    assert len(gaps) == 1
    assert gaps[0].problem == "declared-but-missing"
    assert gaps[0].fatal
    assert "export.build" in str(gaps[0])
    assert "副作用 import" in str(gaps[0])


def test_assert_assembly_raises_with_module_name() -> None:
    """启动期的异常必须点名是哪个模块、缺什么，否则等于没报错。"""
    with pytest.raises(RegistryError, match="export 在 ModuleSpec 里声明了 job 'export.build'"):
        assert_assembly(
            [_spec("export", jobs=("export.build",))],
            jobs={},
            periodic={},
        )


def test_registered_but_undeclared_is_not_fatal() -> None:
    """注册了但没声明：清单不全，不影响运行。

    刻意不阻断启动——测试里局部重置注册表很常见，为此让服务起不来
    比漏一条声明更糟。
    """
    gaps = assembly_gaps(
        [],
        jobs={"export.build": "modules.export.worker"},
        periodic={},
    )

    assert [gap.problem for gap in gaps] == ["registered-but-undeclared"]
    assert not gaps[0].fatal
    assert gaps[0].owner == "export"
    # 不抛 = 不阻断启动
    assert_assembly([], jobs={"export.build": "modules.export.worker"}, periodic={})


def test_framework_owned_assemblies_are_whitelisted() -> None:
    """框架目录（generation/、db/）没有 ModuleSpec，靠白名单豁免而不是靠「名字像」。"""
    jobs = {name: "generation.worker" for name in FRAMEWORK_JOBS}
    periodic = {name: "db.outbox" for name in FRAMEWORK_PERIODIC}

    assert assembly_gaps([], jobs=jobs, periodic=periodic) == []


def test_periodic_name_prefix_does_not_excuse_missing_declaration() -> None:
    """周期任务名必须 `<模块>.<动作>`，但前缀只证明「谁命名得像」。

    名字对了不代表注册过；真正的证明是模块在 `spec.periodic` 里写了它。
    """
    gaps = assembly_gaps([], jobs={}, periodic={"asset.gc": "modules.asset.tasks"})

    assert [gap.problem for gap in gaps] == ["registered-but-undeclared"]
    assert gaps[0].owner == "asset"


def test_real_modules_have_no_gaps() -> None:
    """真实模块的声明必须和注册对得上——这条红就等于「线上少一个 worker」。

    在**子进程**里核对：同进程跑的时候，前面的用例可能调过
    `reset_periodic_jobs()` / `reset_*()`，注册表已经不是「刚从磁盘装配出来」
    的状态。而这个不变式要验证的恰恰是「干净启动时对不对得上」，
    在同进程里验等于验了个假。
    """
    import subprocess
    import sys
    from pathlib import Path

    api_root = Path(__file__).resolve().parents[1]
    script = (
        "from api.assembly import assembly_gaps\n"
        "from app import collect_assembly\n"
        "specs, jobs, periodic = collect_assembly()\n"
        "gaps = assembly_gaps(specs, jobs=jobs, periodic=periodic)\n"
        "assert not gaps, '装配对不上：' + '；'.join(str(g) for g in gaps)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=api_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")


def test_module_files_declare_what_they_register_via_decorator() -> None:
    """代码里出现 `@register_handler("x")` / `@periodic("y")`，就必须在 ModuleSpec 声明。

    这是装配对账补不上的那个洞：新模块写了 `worker.py`、也写了 handler，
    但**既没在 spec 声明、又忘了 `__init__.py` 的副作用 import**。此时
    `assembly_gaps()` 两个方向都看不见它（没注册也没声明），完全静默。
    只有扫源码才能发现「这个文件打算注册东西，但装配链断了」。
    """
    import ast
    from pathlib import Path

    from api.discovery import discover_modules

    modules_dir = Path(__file__).resolve().parents[1] / "src" / "modules"
    specs = {spec.name: spec for spec in discover_modules()}
    problems: list[str] = []

    for path in sorted(modules_dir.glob("*/*.py")):
        module_name = path.parent.name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call) or not deco.args:
                    continue
                arg = deco.args[0]
                if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                    continue
                func = deco.func
                decorator = (
                    func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                )
                spec = specs.get(module_name)
                if spec is None:
                    continue
                if decorator == "register_handler" and arg.value not in spec.jobs:
                    problems.append(f"{module_name}/{path.name}: handler {arg.value!r} 未声明")
                elif decorator == "periodic" and arg.value not in spec.periodic:
                    problems.append(f"{module_name}/{path.name}: 周期任务 {arg.value!r} 未声明")

    assert not problems, "这些装配物既没声明也不会被对账看见 -> " + str(problems)


def _api_route(path: str, *codes: str, wrap: bool = False) -> APIRoute:
    """造一条真实的 `APIRoute`，权限依赖挂在它上面。

    刻意不用鸭子类型的假路由：`permission_gaps` 现在走 `iter_api_routes`，
    它按 `isinstance(route, APIRoute)` 过滤。用假对象会让测试在「守卫已经
    看不见真实路由」时依然绿——守卫本身失效却测不出来，这正是本轮要防的。
    """

    async def endpoint() -> None: ...

    if not codes:
        return APIRoute(path, endpoint)

    dep = require(*codes)
    if not wrap:
        return APIRoute(path, endpoint, dependencies=[Depends(dep)])

    async def wrapper(_: object = Depends(dep)) -> None: ...

    return APIRoute(path, endpoint, dependencies=[Depends(wrapper)])


def test_permission_codes_are_checked_against_declarations() -> None:
    """路由 `require(...)` 的码必须有人声明过。

    这条锁的是「超管能用、别人永远 403」那类 bug 的**启动期**防线：
    AST 守卫在 CI 扫字面量，这里读运行时依赖对象上的标记，
    能覆盖「码从常量拼出来」这种 AST 扫不到的情况。
    """
    from api.assembly import permission_gaps

    # 嵌套一层：真实路由的权限依赖可能挂在更深一层，遍历必须递归
    route = _api_route("/exports", "export:readx", wrap=True)

    gaps = permission_gaps([route], {"export:read"})

    assert len(gaps) == 1, gaps
    assert "export:readx" in gaps[0]
    assert "/exports" in gaps[0]


def test_known_permission_codes_pass() -> None:
    from api.assembly import permission_gaps

    route = _api_route("/exports", "export:read", "export:create")
    assert permission_gaps([route], {"export:read", "export:create"}) == []


def test_permission_gaps_sees_through_included_routers() -> None:
    """`router.py` 里 `include_router` 聚合的子路由，也必须被核对。

    真实事故（本轮发现）：`template` 模块的 `router.py` 只是
    `include_router(replacement_router)` + `include_router(pack_router)`，
    于是它那 20 条路由在启动期核对里**一条都没被扫到**——`permission_gaps`
    返回空列表，检查永远是「没问题」。FastAPI 0.162 起 `include_router`
    放进 `routes` 的是 `_IncludedRouter` 占位，裸迭代必然踩这个坑。
    """
    from api.assembly import permission_gaps

    child = APIRouter(prefix="/children")

    @child.get("/leaf", dependencies=[Depends(require("export:readx"))])
    async def leaf() -> None: ...

    parent = APIRouter(prefix="/templates")
    parent.include_router(child)

    gaps = permission_gaps(parent.routes, {"export:read"})

    assert len(gaps) == 1, f"include 里的路由被漏扫了（守卫永远通过）-> {gaps}"
    assert "export:readx" in gaps[0]
    assert "/templates/children/leaf" in gaps[0]


def test_every_module_route_is_visible_to_permission_check() -> None:
    """每个模块的 `router.routes` 都必须能被展开成真实路由。

    这条是上面那条的**全量**版本：只要有模块用 `include_router` 却仍能被
    展开（`iter_api_routes` 负责），这里的数量就该等于「接口真的有几条」。
    断言用「非空且无占位」，因为占位一旦出现就说明展开逻辑退化了。
    """
    from api.routing import iter_api_routes

    empty = [
        spec.name
        for spec in discover_modules()
        if not list(iter_api_routes(spec.router.routes))
    ]
    assert not empty, f"这些模块的路由一条都展开不出来（权限核对会静默跳过）-> {empty}"
