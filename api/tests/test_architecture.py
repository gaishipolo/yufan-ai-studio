"""架构约束测试。

这些测试不是「验证功能」，而是**用机器守住架构约定** ——
它们会拦住「为了图快直接在核心层 import 业务模块」这类会腐蚀可扩展性的改动。

新增业务功能时这些测试不需要修改；只有当你确实要放宽某条约定时才改。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from api.discovery import discover_modules, reset_discovery_cache
from api.module import ModuleSpec
from core.exceptions import AppError
from core.permissions import catalog
from infra.ai.base import ModelProvider

SRC = Path(__file__).resolve().parents[1] / "src"
MODULES_DIR = SRC / "modules"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
    return found


def _python_files(package: Path) -> list[Path]:
    return [p for p in package.rglob("*.py") if "__pycache__" not in p.parts]


# --------------------------------------------------------------------------- #
# 依赖方向
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("layer", ["core", "db", "infra", "generation"])
def test_lower_layers_do_not_import_modules(layer: str) -> None:
    """core / db / infra / generation 都是底层，绝不能反向依赖业务模块。"""
    offenders: list[str] = []
    for path in _python_files(SRC / layer):
        for imported in _imported_modules(path):
            if imported.startswith("modules"):
                offenders.append(f"{path.relative_to(SRC)} -> {imported}")

    assert not offenders, "底层反向依赖业务模块：\n" + "\n".join(offenders)


API_DIR = SRC / "api"


def test_api_layer_does_not_import_business_modules() -> None:
    """`api/` 是装配层，一个业务模块都不许 import（手册第 0 节第 1 条）。

    这条**曾经没有守卫**：`test_lower_layers_do_not_import_modules` 只参数化了
    core/db/infra/generation，把「api 也要守」留在了文档里。于是
    `api/diagnostics.py` 悄悄 import 了 `modules.asset.models` 与
    `modules.image.models`，`app doctor` 里的几条业务检查一直是这么跑的。

    代价是用一次「删掉 image 模块」测出来的：doctor 在第 5 项上
    `ModuleNotFoundError` 崩掉，后面十几条检查一条都看不到；而且这个崩溃
    **发生在整份报告之前**，运维拿到的是一段 traceback 而不是「哪一项不合格」。

    正确的形状是模块自检从自己家里挂上来（`ModuleSpec.doctor`），诊断层只负责
    按顺序调用。加模块/删模块都不该碰 `api/`——这正是这条守卫要固定的事。
    """
    offenders: list[str] = []
    for path in _python_files(API_DIR):
        for imported in _imported_modules(path):
            if imported == "modules" or imported.startswith("modules."):
                offenders.append(f"{path.relative_to(SRC)} -> {imported}")

    assert not offenders, (
        "api/ 反向依赖业务模块：\n"
        + "\n".join(offenders)
        + "\n正确做法：需要「某个模块特有」的检查/命令，把它变成 ModuleSpec 上的钩子"
        "（doctor / seed / bootstrap / recovery_kinds），由模块自己实现。"
    )


# 顶层脚本（CLI / 种子）允许直接 import 的业务模块。
#
# **现在是空的**：`ModuleSpec.cli` 与 `ModuleSpec.seed` 落地之后，`cli.py` 与
# `seed.py` 都不再认识任何业务模块——建号、清审计、归档迁移搬进了
# `modules/identity/cli.py` 与 `modules/asset/cli.py`，演示数据搬进了各自的
# `modules/*/seed.py`。
#
# 空字典不是「这条守卫没用了」，恰恰相反：它现在是零容忍。谁哪天为了让新功能
# 跑起来在顶层脚本里写一句 `from modules.xxx import ...`，CI 立刻红。
# 真有必要加，就带着**退出条件**一起加进来（那是临时豁免，不是永久许可）。
KNOWN_SCRIPT_MODULE_IMPORTS: dict[str, frozenset[str]] = {}


def test_top_level_scripts_only_import_known_modules() -> None:
    """顶层脚本的业务模块 import 只能来自清单，且清单不许留过期条目。

    「过期条目」同样要红：某个脚本已经不再 import 那个模块，清单里还留着，
    下一个人就会以为它是刻意的。这张表的价值全在**准**，不准就只剩噪音。
    """
    problems: list[str] = []
    for name, allowed in KNOWN_SCRIPT_MODULE_IMPORTS.items():
        path = SRC / name
        actual = {
            imported
            for imported in _imported_modules(path)
            if imported == "modules" or imported.startswith("modules.")
        }
        for imported in sorted(actual - allowed):
            problems.append(f"{name} 新增了业务模块依赖：{imported}（先问是不是设计缺口）")
        for imported in sorted(allowed - actual):
            problems.append(f"{name} 清单已过期：{imported} 早就不 import 了，删掉这一行")

    assert not problems, "顶层脚本的模块依赖清单与代码不一致：\n" + "\n".join(problems)


def test_modules_do_not_import_each_other() -> None:
    """业务模块之间禁止直接 import，共享逻辑必须下移到 core/db/infra/generation。"""
    offenders: list[str] = []
    for module_dir in MODULES_DIR.iterdir():
        if not module_dir.is_dir() or module_dir.name.startswith("_"):
            continue
        for path in _python_files(module_dir):
            for imported in _imported_modules(path):
                if not imported.startswith("modules."):
                    continue
                owner = imported.split(".")[1]
                if owner != module_dir.name:
                    offenders.append(f"{module_dir.name}: {path.name} -> {imported}")

    assert not offenders, "业务模块之间出现横向依赖：\n" + "\n".join(offenders)


# `api` 只提供装配点：模块契约、依赖注入、主体解析与账户状态注入。
# 其它 `api.*` 是装配层内部实现，模块引用会形成反向边，环和启动顺序问题都会回来。
ASSEMBLY_POINTS = ("api.module", "api.deps", "api.accounts", "api.authn")


def test_modules_only_touch_api_assembly_points() -> None:
    offenders: list[str] = []
    for path in _python_files(MODULES_DIR):
        for imported in _imported_modules(path):
            if not (imported == "api" or imported.startswith("api.")):
                continue
            if any(imported == p or imported.startswith(f"{p}.") for p in ASSEMBLY_POINTS):
                continue
            offenders.append(f"{path.relative_to(SRC)} -> {imported}")

    assert not offenders, "业务模块引用了装配层实现，依赖方向应为 api → modules：\n" + "\n".join(
        offenders
    )


# --------------------------------------------------------------------------- #
# 模块自注册
# --------------------------------------------------------------------------- #
def test_every_module_directory_exports_spec() -> None:
    """modules 下每个子包都必须导出 ModuleSpec，否则不会被自动挂载。"""
    reset_discovery_cache()
    specs = discover_modules()
    discovered = {spec.name for spec in specs}

    missing = [
        d.name
        for d in MODULES_DIR.iterdir()
        if d.is_dir() and not d.name.startswith("_") and d.name not in discovered
    ]
    assert not missing, f"以下模块没有导出 module: ModuleSpec -> {missing}"


def test_module_spec_name_matches_directory() -> None:
    for spec in discover_modules():
        assert isinstance(spec, ModuleSpec)
        assert (MODULES_DIR / spec.name / "__init__.py").exists()


def test_every_module_exposes_routes() -> None:
    empty = [spec.name for spec in discover_modules() if not spec.router.routes]
    assert not empty, f"模块没有任何路由（可能是漏了 include_router）-> {empty}"


def test_module_order_is_stable() -> None:
    orders = [(spec.order, spec.name) for spec in discover_modules()]
    assert orders == sorted(orders), "模块顺序不稳定会导致路由注册顺序漂移"


# --------------------------------------------------------------------------- #
# 权限点
# --------------------------------------------------------------------------- #
def test_permissions_are_declared_by_owners() -> None:
    """所有被声明过的权限点都应能在目录里查到（catalog 已去重）。"""
    declared: set[str] = set()
    for spec in discover_modules():
        declared |= set(spec.permission_codes())

    assert declared == set(catalog.codes())
    for code in declared:
        assert ":" in code, f"权限码格式应为 资源:动作，收到 {code}"


def test_every_module_declares_permissions() -> None:
    """不允许「能登录 = 能访问」的模块存在。

    半套 RBAC 比没有 RBAC 更危险：看起来有权限体系，实际有一半接口裸奔。
    只有显式标记 `public=True` 的模块（健康检查等）可以豁免。
    """
    missing = [
        spec.name for spec in discover_modules() if not spec.public and not spec.permission_codes()
    ]
    assert not missing, f"以下模块没有声明任何权限点 -> {missing}"


def test_permission_codes_are_namespaced_by_module() -> None:
    """权限码前缀必须与模块名一致，否则角色配置界面无法按模块分组。"""
    mismatched: list[str] = []
    for spec in discover_modules():
        for code in spec.permission_codes():
            if not code.startswith(f"{spec.name}:"):
                mismatched.append(f"{spec.name} -> {code}")
    assert not mismatched, f"权限码前缀与归属模块不一致 -> {mismatched}"


def _require_calls(package: Path) -> Iterator[ast.Call]:
    """一个模块里所有 `require(...)` 调用（不管实参是字面量还是常量名）。

    用 `rglob` 而不是 `glob`：模块内再分子包（`modules/x/http/`）时，
    `glob("*.py")` 会静默漏掉整个子包，守卫照旧「没问题」。
    """
    for path in _python_files(package):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "require":
                yield node


def _required_codes(package: Path) -> set[str]:
    """AST 提取一个模块里所有 `require("...")` 的权限码。

    上面三条测试都只看「声明侧」。而拼错一个字母、或者从别的模块
    copy 了一个 `require("task:read")` 过来，声明的集合依然自洽，
    症状却是：超级管理员（`*`）一切正常，其他人永远 403。
    启动、doctor、契约生成、既有测试全都不报错——这种"只有一部分人会遇到"
    的问题最难查，所以要在静态分析这一层拦住。

    只认字面量：`require(PERM_READ)` 这种常量写法在这里解不出来，
    由 `test_permission_coverage.py` 的跨文件常量解析覆盖，两者互补。
    """
    return {
        arg.value
        for call in _require_calls(package)
        for arg in call.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    }


def test_required_codes_are_declared_somewhere() -> None:
    """路由里 `require` 的权限码必须在**某个模块**的 `ModuleSpec` 里声明过。

    上面三条测试都只看「声明侧」，没有一条管「路由真的用了什么」。而拼错一个
    字母、或者把 `asset:read` 写成 `assets:read`，声明的集合依然自洽，
    症状却是：超级管理员（`*`）一切正常，其他人永远 403。
    启动、doctor、契约生成、既有测试全都不报错——这种「只有一部分人会遇到」
    的问题最难查，所以在静态分析这一层拦住。

    刻意不要求「必须自己声明」：`template` 的封装接口同时要求 `asset:read`
    是组合语义（要封装素材，先得能看素材），跨模块借用权限码是允许的。
    """
    all_declared = set(catalog.codes())
    problems = [
        f"{spec.name}: require({code!r}) 没有任何模块声明"
        for spec in discover_modules()
        for code in sorted(_required_codes(MODULES_DIR / spec.name))
        if code not in all_declared
    ]
    assert not problems, f"路由用了没人登记的权限码（普通用户会永远 403）-> {problems}"


def test_export_sources_declare_known_permissions() -> None:
    """导出源挂在数据上的权限码也要有人声明。

    上面那条只扫 `require("...")` 字面量。而导出源把权限码放在
    `source.permission` 属性里，静态分析看不到——漏声明的症状一模一样：
    超管能导、别人 403，没有任何一处报错。
    """
    from core.ports.export_sources import export_sources
    from modules.export.sources import AUDIT_EXPORT_SOURCE

    known = set(catalog.codes())
    sources = dict(export_sources())
    sources[AUDIT_EXPORT_SOURCE.name] = AUDIT_EXPORT_SOURCE

    missing = [
        f"{source.name} -> {source.permission}"
        for source in sources.values()
        if source.permission is not None and source.permission not in known
    ]
    assert not missing, f"导出源要求的权限码没人声明（只有超管能导）-> {missing}"


# --------------------------------------------------------------------------- #
# 错误码
# --------------------------------------------------------------------------- #
def test_error_codes_encode_http_status() -> None:
    """`code // 100` 必须等于 http_status，否则前端按码推断状态会错。"""

    def all_subclasses(cls: type) -> set[type]:
        result = set()
        for sub in cls.__subclasses__():
            result |= {sub} | all_subclasses(sub)
        return result

    for cls in all_subclasses(AppError):
        if cls.code == AppError.code and cls.http_status == AppError.http_status:
            continue  # 未覆盖的中间抽象类
        assert cls.code // 100 == cls.http_status, (
            f"{cls.__name__}: code={cls.code} 与 http_status={cls.http_status} 不一致"
        )


# --------------------------------------------------------------------------- #
# 生成框架
# --------------------------------------------------------------------------- #
def test_pipelines_implement_protocol() -> None:
    from generation import pipelines

    assert len(pipelines) > 0, "至少应注册一个生成流水线（video）"
    for kind, pipeline in pipelines:
        assert kind
        assert pipeline.kind == kind
        assert hasattr(pipeline, "capability")
        assert callable(pipeline.store)
        assert callable(pipeline.build_request)


def test_providers_declare_their_models() -> None:
    """供应商必须能解析自己声明的每一个模型（防止厂商改名后路由失效）。"""
    from infra import get_model_router
    from infra.ai.base import Capability

    router = get_model_router()
    for model in router.models():
        assert isinstance(model.capability, Capability)
        provider: ModelProvider = router.provider_for(model.id)
        assert model.id in {m.id for m in provider.models}


def test_model_alias_resolution() -> None:
    """前端历史上按中文 label 传 model，必须能解析回同一供应商。"""
    from infra import get_model_router

    router = get_model_router()
    for model in router.models():
        assert router.provider_for(model.label).name == router.provider_for(model.id).name


# --------------------------------------------------------------------------- #
# 跨模块注入口（port）
#
# 约定写在 `api/ports.py` 的模块注释里。这里只做机械校验：
# 新增一个注入口却没登记 → 红；登记了但形状不全 → 红。
# 目的是让「一共有几个注入口、谁实现它」从"读代码才知道"变成"机器保证"。
# --------------------------------------------------------------------------- #
_PORT_SCAN_DIRS = (SRC / "api", SRC / "core", SRC / "core" / "ports")


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _builds_port(tree: ast.Module) -> bool:
    """模块里是否出现了 `Port(...)` 实例——机制层面的证据。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _base_name(node.func) == "Port":
            return True
    return False


def _looks_like_port_module(path: Path) -> bool:
    """是不是一个注入口：**机制证据优先，形状做兜底**。

    旧判据只看「有没有 `register_*` / `set_*` 前缀的函数」，于是同一个意思换个
    动词就整份漏掉：`core/ports/media.py` 最初写成 `bind_asset_lookup`（`bind_`
    在本仓是「声明来源」的语义，`bind_asset_reference_sources` 是那样用的），
    守卫认定它不是注入口 → 既没进 `PORT_CATALOG`、也不被形状检查，结果是
    「素材模块没装配」时 `app modules` 与 `doctor` 全绿，只有真正调 prepare
    才拿到 503。

    所以改成三条，任一成立即算注入口：

    1. 模块里出现 `Port(...)` 实例（合规写法的直接证据，与函数叫什么无关）；
    2. `reset_*` 与 `has_*` 同时存在（这两个是注入口特有的形状，业务代码里
       几乎不会出现——扫描目录也只有 `api/` 与 `core/`，不含业务模块）；
    3. 旧形状：`register_*` / `set_*` + `reset_*` + `has_*` 齐全。

    识别出来之后，**形状是否合规交给 `test_port_modules_expose_the_full_shape`**
    ——它要求有且只有一个 `register_*` / `set_*`，`bind_` 到那里会明确报错。
    两道守卫分工：这一道负责「别漏」，那一道负责「别歪」。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if _builds_port(tree):
        return True
    functions = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    has_reset = any(n.startswith("reset_") for n in functions)
    has_probe = any(n.startswith("has_") for n in functions)
    has_register = any(n.startswith(("register_", "set_")) for n in functions)
    return (has_reset and has_probe) or (has_register and has_reset and has_probe)


def _dotted(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts)


def test_every_port_is_catalogued() -> None:
    from api.ports import PORT_CATALOG

    catalogued = {spec.module for spec in PORT_CATALOG}
    # `rglob` 而不是 `glob`：注入口挪进子包（`core/ports/providers/`）时，
    # 扁平 glob 会整份漏掉，守卫照旧「没问题」。这是同一类失败模式的第 4 次
    # 出现（权限核对、限流策略、`require` 扫描都栽过）。
    detected = sorted(
        {
            _dotted(path)
            for directory in _PORT_SCAN_DIRS
            for path in directory.rglob("*.py")
            if "__pycache__" not in path.parts and _looks_like_port_module(path)
        }
    )
    missing = [name for name in detected if name not in catalogued]
    assert not missing, (
        f"这些文件是注入口但没有登记进 api/ports.py 的 PORT_CATALOG：{missing}。"
        "没登记的注入口会让「谁会实现它、没接上是什么行为」再次变成口口相传"
    )
    assert len(detected) >= len(PORT_CATALOG), (
        f"只认出 {len(detected)} 个注入口文件，少于目录里的 {len(PORT_CATALOG)} 条："
        "扫描口径退化了（识别不出来 = 上面那条永远通过）"
    )


def test_port_modules_expose_the_full_shape() -> None:
    """注入口必须自带 register / reset / has —— 缺 has 就没法在启动日志里留痕。"""
    import importlib

    from api.ports import PORT_CATALOG

    problems: list[str] = []
    for spec in PORT_CATALOG:
        module = importlib.import_module(spec.module)
        names = set(vars(module))
        if spec.protocol:
            protocol = getattr(module, spec.protocol, None)
            if not isinstance(protocol, type):
                problems.append(f"{spec.name}: {spec.module} 里没有协议类 {spec.protocol}")
        # 类型别名式注入口用 set_*，具名协议式用 register_*，两者都算一个注册入口
        registers = sorted(n for n in names if n.startswith(("register_", "set_")))
        if len(registers) != 1:
            problems.append(f"{spec.name}: 应有且只有一个 register_*/set_*，实际 {registers}")
        for prefix in ("reset_", "has_"):
            if not any(n.startswith(prefix) for n in names):
                problems.append(f"{spec.name}: 缺少 {prefix}* 函数")
    assert not problems, "注入口形状不完整：\n" + "\n".join(problems)


def test_port_names_are_unique() -> None:
    from api.ports import PORT_CATALOG

    names = [spec.name for spec in PORT_CATALOG]
    assert len(names) == len(set(names)), f"注入口重名：{names}"


# --------------------------------------------------------------------------- #
# 基础设施驱动
# --------------------------------------------------------------------------- #
def test_archive_storage_only_reached_through_factory() -> None:
    """归档盘只能经 `get_archive_storage()` 获取，不许在别处直接 new 具体实现。

    写死一处就少一个扩展点：要换 S3 / MinIO 时得靠 grep 才能找全调用点，
    而找漏的那一处会在换驱动后静默继续写本地盘。
    """
    offenders: list[str] = []
    for path in _python_files(SRC):
        if path.parts[-2:] == ("archive", "local.py"):  # 实现自己，允许
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Call) and _base_name(node.func) == "LocalArchiveStorage"
            for node in ast.walk(tree)
        ):
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, (
        f"这些文件直接实例化了归档实现：{offenders}。请改用 infra.archive.get_archive_storage()"
    )


def test_archive_enumeration_goes_through_list_keys() -> None:
    """归档枚举只能走 `list_keys`，不许在 `root()` 上 `glob`。

    `root()` 是「本地驱动才有的东西」：写成 `(storage.root() / "catalog").glob(...)`
    时，本地一切正常，切到 S3/MinIO 就整块抛 `UpstreamError`（封装库、任务中心一起挂）。
    这条守卫是 T17 的防复发措施——枚举漏一处，远端切换就失败一次。
    """
    pattern = re.compile(r"\.root\(\)[^\n]*\.(?:r?glob)\(")
    offenders = [
        f"{path.relative_to(SRC)}:{index}"
        for path in _python_files(SRC)
        if path.parts[-2:] != ("archive", "local.py")  # 实现自己，允许
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, (
        f"这些位置在归档根上做目录枚举：{offenders}。"
        "请改用 storage.list_keys(prefix, suffix=)，否则远端驱动下会直接抛错"
    )


_PERIODIC_DECORATOR = re.compile(r"@periodic\(\s*[\"']([^\"']+)[\"']")


def test_periodic_jobs_are_namespaced_by_module() -> None:
    """周期任务名必须以**所在模块目录**开头，`app modules` 才能按模块分组。

    这里静态扫源码而不是读运行时注册表：注册表是 import 副作用，测试顺序一变就为空，
    检查会悄悄失效——守卫测试最忌讳这种「永远绿」。
    """
    modules = {d.name for d in MODULES_DIR.iterdir() if d.is_dir() and not d.name.startswith("_")}
    bad: list[str] = []
    for path in _python_files(MODULES_DIR):
        for name in _PERIODIC_DECORATOR.findall(path.read_text(encoding="utf-8")):
            owner = path.relative_to(MODULES_DIR).parts[0]
            if name.split(".", 1)[0] not in modules or not name.startswith(f"{owner}."):
                bad.append(f"{path.relative_to(MODULES_DIR)} -> {name}")
    assert not bad, f"周期任务命名必须 `<模块>.<动作>`：{bad}"


# --------------------------------------------------------------------------- #
# 模块 × 插槽矩阵（R11 第八节）
#
# 之前的守卫只检查「插槽有没有造出来」。R11 的两条 🔴 都是另一个形状：
# 新模块接齐了「让它跑起来」的插槽，却没接「出事后能恢复」的插槽，
# 292 个用例、ruff、mypy、契约检查全部照常通过。按维度数插槽发现不了，
# 必须按每个 kind 对账归属模块。
# --------------------------------------------------------------------------- #
def test_every_pipeline_kind_declares_a_recovery_slot() -> None:
    from api.discovery import active_modules
    from generation.pipeline import get_pipeline, pipeline_kinds

    specs = active_modules()  # 副作用：import 所有模块，填满 pipeline 注册表
    kinds = pipeline_kinds()
    assert kinds, "生成流水线注册表为空：模块没被 import，这条守卫会永远绿"

    declared = {kind for spec in specs for kind in spec.recovery_kinds}
    missing = [kind for kind in kinds if kind not in declared]
    assert not missing, (
        f"这些生成 kind 没有任何模块声明负责恢复：{missing}。"
        "在 ModuleSpec.recovery_kinds 里声明（框架恢复），或提供自己的 startup"
    )

    for spec in specs:
        if spec.startup is not None or not spec.recovery_kinds:
            continue  # 模块自带恢复器，框架不插手
        for kind in spec.recovery_kinds:
            assert hasattr(get_pipeline(kind), "list_unfinished"), (
                f"模块 {spec.name} 依赖框架恢复 kind={kind}，但流水线没有 "
                "list_unfinished(session, limit)"
            )


def test_every_kind_has_a_user_retry_exit() -> None:
    """`SUBMIT_UNKNOWN_MESSAGE` 写着「请核对后手动重试」，不能是空头支票。

    runner 对 submit_unknown 的记录一律判死，此后 `handle_submit` 的终态早退
    让它再也动不了；没有 retry 出口时这条记录永久死锁（R11-02 现象 A）。

    判据落在**行为**上：`tests/test_user_retry_exits.py` 的 `EXIT_CASES` 每个条目
    都是一次真实调用（造「提交结果未知」的现场 → 走用户点得到的入口 → 断言不抛
    Conflict），这里只对账「每个 kind 都有条目」。

    上一版用三个正则判文本，其中 `"retry_count" in text` 专为 template 加的：
    那个字段计的是**自动**重试，还在被拦下之前被 `schedule_state` 清零，连
    「用户点得动」都证明不了。于是守卫两边都 True，而 template 那条记录点一百次
    也只是同一句 Conflict（R14-01）——守卫验证「有没有」，没验证「通不通」。
    """
    from test_user_retry_exits import EXIT_CASES

    from api.discovery import active_modules
    from generation.pipeline import pipeline_kinds

    active_modules()  # 副作用：import 所有模块，填满 pipeline 注册表
    kinds = set(pipeline_kinds())
    assert kinds, "生成流水线注册表为空：模块没被 import，这条守卫会永远绿"

    missing = sorted(kinds - set(EXIT_CASES))
    assert not missing, (
        f"这些 kind 没有登记用户自救出口：{missing}。补一个能把「提交结果未知」"
        "放行的入口，并在 tests/test_user_retry_exits.py 的 EXIT_CASES 里登记"
    )
    stale = sorted(set(EXIT_CASES) - kinds)
    assert not stale, f"EXIT_CASES 里有已经不存在的 kind：{stale}"


# --------------------------------------------------------------------------- #
# 素材引用来源完备性：唯一「漏一处就静默永久丢数据」的扩展点
# --------------------------------------------------------------------------- #
_REFERENCE_FIELDS = frozenset({"reference_asset_ids", "reference_ids", "referenceIds"})


def _reference_field_files() -> list[str]:
    """找出所有声明了「装着 assets.id 的集合」字段的源码文件。

    只看模型/契约层的字段定义（赋值 + 名字匹配），不看读它的 checker。
    新增一个把 `assets.id` 存进自己表或 JSON 的模块，这个集合就会多一项。
    """
    found: set[str] = set()
    for path in _python_files(SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.append(node.target.id)
            elif isinstance(node, ast.Assign):
                names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
            if any(name in _REFERENCE_FIELDS for name in names):
                found.add(path.relative_to(SRC).as_posix())
                break
    return sorted(found)


def test_every_asset_reference_writer_declares_a_source() -> None:
    """任何写入 `assets.id` 的模块都必须在 ModuleSpec 里声明引用来源。

    删除保护靠 `core/ports/assets.py` 聚合所有来源；漏登记一个来源，删除会
    返回成功、引用方从此裂图，是不可逆且没有告警的失败。所以既要有运行时
    fail-closed，也要在 CI 里对账「写入点 → 声明」这条静态链。
    """
    from api.discovery import active_modules

    specs = active_modules()
    declared = {
        source.location.split(":", 1)[0]
        for spec in specs
        for source in spec.asset_reference_sources
    }
    by_module = {
        spec.name for spec in specs if spec.asset_reference_sources
    }

    uncovered: list[str] = []
    for relative in _reference_field_files():
        parts = relative.split("/")
        if parts[0] == "modules":
            if parts[1] not in by_module:
                uncovered.append(f"{relative}（模块 {parts[1]} 未声明来源）")
        elif relative not in declared:
            # core 层定义、被业务模块持久化的共享模型，必须由某个声明显式指认
            uncovered.append(f"{relative}（共享模型，没有任何来源声明指向它）")

    missing_files = sorted(path for path in declared if not (SRC / path).is_file())
    details = uncovered + [f"声明指向不存在的文件：{path}" for path in missing_files]
    assert not details, (
        "引用来源对账失败：\n"
        + "\n".join(f"  - {item}" for item in details)
        + "\n在对应模块的 ModuleSpec.asset_reference_sources 里补声明，并在 "
        "core/ports/assets.py 的聚合口注册实现；否则删除会被 fail-closed 挡住。"
    )


def test_reference_sources_are_complete_after_discovery() -> None:
    """discover_modules 之后必须零缺口：声明集合 ⊆ 已注册实现。"""
    from api.discovery import active_modules
    from core.ports import assets as port

    port.reset_asset_reference_checker()
    specs = active_modules()
    # 模块 import 期已注册过；测试间可能清空了全局表，这里按声明重新装配一次。
    for spec in specs:
        if spec.asset_reference_sources:
            port.bind_asset_reference_sources(spec.name, spec.asset_reference_sources)
    assert port.declared_reference_sources(), "期望集合为空：没有模块声明引用来源"
    assert not port.reference_source_gaps(), (
        f"删除会被拒绝：{port.reference_source_gaps()}"
    )


# --------------------------------------------------------------------------- #
# v2 资产（`library_assets.id`）引用来源完备性：与上面两条**不能合并**
# --------------------------------------------------------------------------- #
#: 装着 v2 资产 id 的字段名。**精确匹配**，不做子串匹配 —— 与 `_REFERENCE_FIELDS`
#: （v1 `assets.id`）是两套 id 空间，混在一起会把「A 引用了同名 id」当成「B 被引用」。
#:
#: 刻意**不含** `text_library_ids` / `textLibraryIds`：文本素材的快照冻结了正文
#: 本身，删掉资产既不影响历史展示，也不影响已经注入过的提示词。把它算成引用会
#: 让「用过一次的文案永远删不掉」——过度保护的结果是用户要求关掉整道闸，比漏保护更糟
#: （判据写在 `modules/image/library_references.py` 的 docstring 里）。
#:
#: 也**不含** `pack_id` / `extra_pack_ids`：那两个是模板库自己的
#: `TemplatePackVersion` 主键（`modules/template/schema.py`），与 v2 封装无关。
#: 真正指向 v2 封装的字段按 §23.5 的约定叫 `library_pack_id`。
_LIBRARY_REFERENCE_FIELDS = frozenset(
    {
        "reference_library_ids",
        "referenceLibraryIds",
        "library_pack_id",
        "libraryPackId",
    }
)


def _library_reference_field_files() -> list[str]:
    """找出所有声明了「装着 v2 资产 id 的集合」字段的源码文件。

    只看模型/契约层的字段定义（赋值 + 精确名字匹配），不看读它的 checker。
    新增一处把 `library_assets.id` 存进自己表或 JSON 的模块，这个集合就会多一项。
    """
    found: set[str] = set()
    for path in _python_files(SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.append(node.target.id)
            elif isinstance(node, ast.Assign):
                names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
            if any(name in _LIBRARY_REFERENCE_FIELDS for name in names):
                found.add(path.relative_to(SRC).as_posix())
                break
    return sorted(found)


def test_every_library_reference_writer_declares_a_source() -> None:
    """任何写入 v2 资产 id 的模块都必须在 ModuleSpec 里声明引用来源。

    v1 那条守卫（`test_every_asset_reference_writer_declares_a_source`）只看
    `assets.id`，v2 的 `library_assets.id` 完全不在它的视野里 —— 于是「模板/任务
    引用了某张 v2 图，用户把图彻底删掉」这条路一直是静默裂图。这条补上它。

    失败方向与 v1 一致：漏声明 → `ask_library_references()` 返回的映射里没有
    这个来源 → 删除照常成功 → 引用方裂图，不可逆且没有告警。
    """
    from api.discovery import active_modules

    specs = active_modules()
    declared = {
        source.location.split(":", 1)[0]
        for spec in specs
        for source in spec.library_reference_sources
    }
    by_module = {spec.name for spec in specs if spec.library_reference_sources}

    uncovered: list[str] = []
    for relative in _library_reference_field_files():
        parts = relative.split("/")
        if parts[0] == "modules":
            if parts[1] not in by_module:
                uncovered.append(f"{relative}（模块 {parts[1]} 未声明来源）")
        elif relative not in declared:
            # core 层定义、被业务模块持久化的共享契约，必须由某个声明显式指认
            uncovered.append(f"{relative}（共享契约，没有任何来源声明指向它）")

    missing_files = sorted(path for path in declared if not (SRC / path).is_file())
    details = uncovered + [f"声明指向不存在的文件：{path}" for path in missing_files]
    assert not details, (
        "v2 引用来源对账失败：\n"
        + "\n".join(f"  - {item}" for item in details)
        + "\n在对应模块的 ModuleSpec.library_reference_sources 里补声明，并在 "
        "core/ports/library_references.py 的聚合口注册实现；否则 v2 资产的彻底删除"
        "会放行一次不可逆的裂图。"
    )


def test_library_reference_sources_are_complete_after_discovery() -> None:
    """discover_modules 之后必须零缺口：v2 声明集合 ⊆ 已注册实现。"""
    from api.discovery import active_modules
    from core.ports import library_references as port

    port.reset_library_references()
    specs = active_modules()
    # 模块 import 期已注册过；测试间可能清空了全局表，这里按声明重新装配一次。
    for spec in specs:
        if spec.library_reference_sources:
            port.bind_library_reference_sources(spec.name, spec.library_reference_sources)
    assert port.declared_library_reference_sources(), "期望集合为空：没有模块声明 v2 引用来源"
    assert port.library_references_ready(), (
        f"v2 资产的彻底删除会被拒绝：{port.library_reference_gaps()}"
    )


# --------------------------------------------------------------------------- #
# 事务边界
# --------------------------------------------------------------------------- #
def test_routers_do_not_manage_transactions() -> None:
    """路由只做协议转换：`commit()` / `rollback()` 属于 service。

    为什么值得一条架构守卫：`commit()` 出现在路由里时，「一个用例一次事务」就
    变成了「一次调用一次事务」，而事务范围决定了三件事能不能成立——
    ① outbox 的 `record()` 与业务行同事务；② 校验失败时不会留下半条数据；
    ③ 一个 service 方法被另一个方法复用时不会在中途提交。
    这类问题在 code review 里很容易被当成风格问题放过。
    """
    offenders: list[str] = []
    for path in _python_files(MODULES_DIR):
        if path.name != "router.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in {"commit", "rollback", "flush"}:
                offenders.append(
                    f"{path.relative_to(SRC)}:{node.lineno} -> {node.func.attr}()"
                )
    assert not offenders, (
        "路由层出现事务操作（事务边界必须留在 service）：\n"
        + "\n".join(f"  - {item}" for item in offenders)
    )


def test_port_detection_does_not_depend_on_the_verb(tmp_path: Path) -> None:
    """守卫靠「机制」认注入口，不靠函数名喊什么。

    `core/ports/media.py` 最初写成 `bind_asset_lookup`。`bind_` 在本仓是
    「声明一个来源」的语义（`bind_asset_reference_sources`），不是「注册实现」；
    旧判据只看 `register_` / `set_` 前缀，于是这个文件既没进 `PORT_CATALOG`、
    也不被形状检查，结果「素材模块没装配」时 `app modules` 与 `doctor` 全绿，
    只有真正调用 prepare 才拿到 503。

    这条用例钉住「换个动词也照样认出来」，免得下次又靠命名约定兜底。
    """
    hand_rolled = tmp_path / "hand_rolled.py"
    hand_rolled.write_text(
        "_impl = None\n"
        "\n"
        "def bind_widget(port):\n"
        "    global _impl\n"
        "    _impl = port\n"
        "\n"
        "def has_widget():\n"
        "    return _impl is not None\n"
        "\n"
        "def reset_widget():\n"
        "    global _impl\n"
        "    _impl = None\n",
        encoding="utf-8",
    )
    assert _looks_like_port_module(hand_rolled), (
        "手写注册表（`_impl` 全局 + bind_/has_/reset_）也是注入口，"
        "识别不出来就意味着它不会出现在 app modules 与启动自检里"
    )


# --------------------------------------------------------------------------- #
# 守卫自身的有效性
#
# 上面几十条守卫的共同形状是「扫一遍，有问题就报」。它们的失效方式也一样：
# **扫不到东西时不报错**——目录改名、glob 改成不匹配当前布局、过滤条件写反，
# 结果都是「零个问题」，看起来和「全都没问题」完全一样。
#
# 这一类失败在本仓已经真实发生过 4 次：权限核对的 include 盲区、`require`
# 扫描的扁平 glob、注入口识别的动词依赖、限流策略只看 `app.routes`。
# 所以给每条扫描加一条「必须真的扫到东西」的元测试。阈值按当前规模取整，
# 缩小到这个数以下时先确认是重构还是退化了，再改数字。
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("layer", ["core", "db", "infra", "generation", "api", "modules"])
def test_layer_directories_are_scannable(layer: str) -> None:
    """层目录必须真的有 .py 文件，否则那几条「不许反向 import」的守卫是空的。"""
    files = _python_files(SRC / layer)
    assert files, f"{layer}/ 下扫描不到任何 .py：依赖方向守卫变成永远通过的检查"


def test_require_scan_is_not_vacuous() -> None:
    """`require(...)` 扫描要有产出。

    `_required_codes` 从 `glob` 换成 `rglob`、或者模块目录结构变化时，
    它可能一个调用都扫不到，而 `test_required_codes_are_declared_somewhere`
    会安静地绿着——守卫失效与守卫通过必须能被区分开。

    两个数都要看：调用**处数**（不受「用常量还是字面量」影响）和字面量**码数**
    （`require(PERM_READ)` 解不出来，所以 18 个字面量码是对的，别拿它跟 33 比）。
    """
    specs = discover_modules()
    calls = sum(1 for spec in specs for _ in _require_calls(MODULES_DIR / spec.name))
    codes = {code for spec in specs for code in _required_codes(MODULES_DIR / spec.name)}

    assert calls >= 25, (
        f"只在源码里找到 {calls} 处 `require(...)` 调用（预期 ≥25）："
        "扫描口径退化了，路由权限守卫实际上没在检查"
    )
    assert len(codes) >= 15, f"只扫到 {len(codes)} 个字面量权限码（预期 ≥15）：解析口径变了"
    assert "asset:read" in codes, "连确定以字面量出现的 asset:read 都没扫到，文件定位有问题"
