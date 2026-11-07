"""架构约束测试。

这些测试不是「验证功能」，而是**用机器守住架构约定** ——
它们会拦住「为了图快直接在核心层 import 业务模块」这类会腐蚀可扩展性的改动。

新增业务功能时这些测试不需要修改；只有当你确实要放宽某条约定时才改。
"""

from __future__ import annotations

import ast
import re
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


def _looks_like_port_module(path: Path) -> bool:
    """「注册 / 复位 / 是否已接上」三个动作齐全 = 一个注入口。

    不再要求具名 `Protocol` 子类 + `register_*`：`core.audit` 用的是
    `Callable` 类型别名 + `set_audit_sink`，按旧形状识别时它既不在清单、
    也不在扫描范围、也不在启动日志里——审计写入端没接上会静默丢数据，
    而所有守卫照常全绿（R11-05）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    has_register = any(n.startswith(("register_", "set_")) for n in functions)
    has_reset = any(n.startswith("reset_") for n in functions)
    has_probe = any(n.startswith("has_") for n in functions)
    return has_register and has_reset and has_probe


def _dotted(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts)


def test_every_port_is_catalogued() -> None:
    from api.ports import PORT_CATALOG

    catalogued = {spec.module for spec in PORT_CATALOG}
    missing = [
        _dotted(path)
        for directory in _PORT_SCAN_DIRS
        for path in sorted(directory.glob("*.py"))
        if _looks_like_port_module(path) and _dotted(path) not in catalogued
    ]
    assert not missing, (
        f"这些文件是注入口但没有登记进 api/ports.py 的 PORT_CATALOG：{missing}。"
        "没登记的注入口会让「谁会实现它、没接上是什么行为」再次变成口口相传"
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
