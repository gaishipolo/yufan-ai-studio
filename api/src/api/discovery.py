"""模块自动发现。

扫描 `modules` 下的所有子包，凡是导出了 `module: ModuleSpec` 的即为一个模块。

为什么不用手写清单：
两个人各加一个模块，如果都在 `registry.py` 里追加一行，必然产生 git 冲突；
而且「忘记加一行」的症状是接口 404，排查成本高。目录扫描把这件事变成零操作。

约定（唯一需要记住的规则）：
    在 `src/modules/<name>/__init__.py` 里写
        module = ModuleSpec(name="<name>", router=router, ...)

发现顺序：`(order, name)`，保证路由注册稳定可复现。
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import lru_cache
from types import ModuleType

from api.module import ModuleSpec
from core.exceptions import AppError
from core.logging import get_logger
from core.permissions import catalog
from core.ports.assets import declare_expected_reference_sources

logger = get_logger(__name__)

MODULES_PACKAGE = "modules"
_IGNORED_PREFIXES = ("_", ".")


def _iter_module_packages(package: ModuleType) -> list[str]:
    names: list[str] = []
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith(_IGNORED_PREFIXES) or not info.ispkg:
            continue
        names.append(info.name)
    return sorted(names)


def _load_spec(qualified: str) -> ModuleSpec | None:
    try:
        mod = importlib.import_module(qualified)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        # 模块 import 期报错必须明确指向是哪个模块，否则极难排查
        raise RuntimeError(f"导入模块 {qualified} 失败: {exc}") from exc

    spec = getattr(mod, "module", None)
    if spec is None:
        logger.debug("%s 未导出 module，跳过（非业务模块包）", qualified)
        return None
    if not isinstance(spec, ModuleSpec):
        raise TypeError(
            f"{qualified}.module 必须是 ModuleSpec，收到 {type(spec).__name__}"
        )
    return spec


@lru_cache
def discover_modules(package_name: str = MODULES_PACKAGE) -> tuple[ModuleSpec, ...]:
    """发现并加载所有业务模块（结果缓存，进程内只扫一次）。"""
    package = importlib.import_module(package_name)
    specs: list[ModuleSpec] = []

    for name in _iter_module_packages(package):
        spec = _load_spec(f"{package_name}.{name}")
        if spec is None:
            continue
        if spec.name != name:
            # 名字不一致会让日志/配置对不上，尽早暴露
            raise ValueError(f"模块目录 {name} 与 ModuleSpec.name={spec.name} 不一致")
        specs.append(spec)

    specs.sort(key=lambda s: (s.order, s.name))

    for spec in specs:
        if spec.permissions:
            catalog.declare(*spec.permissions)

    # 引用来源是「必须接上」的声明：在这里并入期望集合，删除接口才能做完备性对账。
    # 模块 import 期注册的是实现，两者缺一都会让删除 fail-closed（503）。
    declare_expected_reference_sources(specs)

    logger.info(
        "已发现 %d 个模块: %s", len(specs), ", ".join(s.name for s in specs)
    )
    return tuple(specs)


def active_modules() -> tuple[ModuleSpec, ...]:
    specs = discover_modules()
    # discover_modules 有缓存；测试可能清空过注入口，这里无条件再声明一次（幂等）。
    declare_expected_reference_sources(specs)
    return tuple(spec for spec in specs if spec.enabled)


def reset_discovery_cache() -> None:
    """仅测试使用：允许新增/删除模块后重新扫描。"""
    discover_modules.cache_clear()
    catalog.clear()


def describe() -> list[dict[str, object]]:
    """架构清单，供 `app modules` 命令与文档自检使用。"""
    return [
        {
            "name": spec.name,
            "label": spec.label,
            "order": spec.order,
            "enabled": spec.enabled,
            "routes": len(spec.router.routes),
            "permissions": sorted(spec.permission_codes()),
        }
        for spec in discover_modules()
    ]
