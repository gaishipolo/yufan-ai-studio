"""架构约束测试。

这些测试不是「验证功能」，而是**用机器守住架构约定** ——
它们会拦住「为了图快直接在核心层 import 业务模块」这类会腐蚀可扩展性的改动。

新增业务功能时这些测试不需要修改；只有当你确实要放宽某条约定时才改。
"""

from __future__ import annotations

import ast
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
                    offenders.append(
                        f"{module_dir.name}: {path.name} -> {imported}"
                    )

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

    assert not offenders, (
        "业务模块引用了装配层实现，依赖方向应为 api → modules：\n" + "\n".join(offenders)
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
        spec.name
        for spec in discover_modules()
        if not spec.public and not spec.permission_codes()
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
