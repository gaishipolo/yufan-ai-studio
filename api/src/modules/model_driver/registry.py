"""驱动自动载入：扫描 `drivers/` 目录，把每个单文件驱动接到模型路由上。

## 为什么注册到 `ModelRouter` 而不是另起一个注册表

`generation.runner.handle_submit()` 里是 `get_model_router().provider_for(record.model)`，
这是整条编排链（提交 / 轮询 / 重试 / 超时 / 启动恢复）唯一找供应商的地方。
驱动只有进这个路由，才能一行框架代码都不改地复用完整编排。

## 幂等与自愈

`get_model_router()` 是进程单例，测试里会被 `reset_model_router()` 换掉。
所以 `ensure_drivers_registered()` 不靠「加载过一次」的布尔标志，而是每次
比对「我发现的驱动名」与「路由里已经有的名字」，缺哪个补哪个。
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, replace
from typing import Any

from core.logging import get_logger
from core.ports.model_generation import DriverSummary
from infra import get_model_router
from infra.ai.base import ModelProvider
from modules.model_driver import spec as standard

logger = get_logger(__name__)

_DRIVERS_PACKAGE = "modules.model_driver.drivers"


@dataclass(frozen=True, slots=True)
class _LoadedDriver:
    provider: ModelProvider
    summary: DriverSummary


_state: dict[str, _LoadedDriver] | None = None
_registered: set[str] = set()
_errors: list[str] = []


def _iter_driver_modules() -> list[tuple[str, str]]:
    package = importlib.import_module(_DRIVERS_PACKAGE)
    found = {
        name: f"{_DRIVERS_PACKAGE}.{name}"
        for _, name, _ in pkgutil.iter_modules(package.__path__)
        if not name.startswith("_")
    }
    return sorted(found.items())


def _instantiate(factory: Any) -> Any:
    """`Driver` 可以是类、工厂函数，或已经实例化好的对象。"""
    if isinstance(factory, type) or callable(factory):
        return factory()
    return factory


def _discover() -> None:
    """扫描并校验所有驱动文件。单个文件出错只记一条，不中断其他驱动。"""
    global _state
    loaded: dict[str, _LoadedDriver] = {}
    for file_name, module_path in _iter_driver_modules():
        try:
            module = importlib.import_module(module_path)
            factory = getattr(module, "Driver", None)
            if factory is None:
                raise TypeError("文件缺少 Driver 定义（Driver = <实现类>）")
            provider = _instantiate(factory)
            problems = standard.validate_driver(provider)
            if problems:
                raise TypeError("；".join(problems))

            meta = getattr(module, "META", standard.DEFAULT_META)
            if not isinstance(meta, standard.DriverMeta):
                meta = standard.DEFAULT_META
            summary = DriverSummary(
                name=provider.name,
                path=f"{module_path.replace('.', '/')}.py",
                label=meta.label or provider.name,
                version=meta.version,
                capabilities=tuple(sorted(c.value for c in provider.capabilities)),
                model_count=len(provider.models),
                supports_poll=bool(getattr(provider, "supports_poll", False)),
                loaded=True,
            )
            if provider.name in loaded:
                raise TypeError(f"驱动名 {provider.name!r} 与 {file_name}.py 之前的驱动重复")
            loaded[provider.name] = _LoadedDriver(provider=provider, summary=summary)
        except Exception as exc:  # noqa: BLE001 - 第三方驱动文件不该让整个服务起不来
            message = f"{file_name}.py: {exc}"
            _errors.append(message)
            logger.error("模型驱动加载失败 %s", message)
            loaded[f"__failed__{file_name}"] = _LoadedDriver(
                provider=None,  # type: ignore[arg-type]
                summary=DriverSummary(
                    name=file_name,
                    path=f"{_DRIVERS_PACKAGE.replace('.', '/')}/{file_name}.py",
                    label=file_name,
                    version="",
                    capabilities=(),
                    model_count=0,
                    supports_poll=False,
                    loaded=False,
                    error=str(exc),
                ),
            )
    _state = loaded


def ensure_drivers_registered() -> None:
    """把已发现的驱动补进 `ModelRouter`。可重复调用，路由被重置后能自愈。"""
    if _state is None:
        _discover()

    router = get_model_router()
    existing = set(router.provider_names())
    for name, item in (_state or {}).items():
        if item.provider is None:
            continue
        if name in _registered and name in existing:
            continue
        if name in existing:
            # 路由里已经有同名：可能是我们上一轮注册的实例（路由单例没换，
            # 而 reset_drivers() 让 Driver 类重新实例化了一次），也可能是
            # 另一轮装配留下的同名实例。按**模型清单**区分：一致就是同一个
            # 驱动的等价实例，复用路由里那份；不一致才是真重名。
            declared = {spec.id for spec in item.provider.models}
            routed = {spec.id for spec in router.models() if spec.provider == name}
            if declared and declared == routed:
                try:
                    resolved = router.provider_for(item.provider.models[0].id)
                except Exception:  # noqa: BLE001 - 解析不了就当真冲突
                    resolved = None
                if resolved is not None and _state is not None:
                    _state[name] = replace(item, provider=resolved)
                _registered.add(name)
                continue
            message = (
                f"驱动名 {name!r} 已被加载但模型清单不同（drivers/ 声明 {sorted(declared)}，"
                f"路由已有 {sorted(routed)}）：请改名或核对已注册的驱动"
            )
            if message not in _errors:
                _errors.append(message)
            continue
        try:
            router.register(item.provider)
        except Exception as exc:  # noqa: BLE001 - 模型 id 撞车不能拖垮整个路由
            message = f"驱动 {name!r} 注册失败：{exc}"
            if message not in _errors:
                _errors.append(message)
            if _state is not None:
                _state[name] = replace(
                    item, summary=replace(item.summary, loaded=False, error=str(exc))
                )
            logger.error("模型驱动注册失败 %s", message)
        else:
            _registered.add(name)
            logger.info(
                "已自动载入模型驱动 %s（%d 个模型）", name, item.summary.model_count
            )


def driver_summaries() -> tuple[DriverSummary, ...]:
    """驱动清单（含加载失败项），按名字排序。"""
    ensure_drivers_registered()
    items = [item.summary for item in (_state or {}).values()]
    return tuple(sorted(items, key=lambda item: item.name))


def load_errors() -> tuple[str, ...]:
    ensure_drivers_registered()
    return tuple(_errors)


def reset_drivers() -> None:
    """仅测试使用：清掉发现结果，下次调用会重新扫描目录。

    不碰 `ModelRouter` 的单例——它归 `infra.ai.reset_model_router()` 管。
    """
    global _state
    _state = None
    _registered.clear()
    _errors.clear()


# --------------------------------------------------------------------- 示例驱动
# 示例驱动的文件名以下划线开头，`_iter_driver_modules()` 会跳过它们，于是
# **不自动载入**：示例模型不进模型目录、不进前端选择器、不进 `app drivers list`。
# 以前它们叫 `mock_async.py` / `mock_sync.py`，于是真的被扫进来了——用户在
# 生成工作台里能选中「示例同步生图」，提交后只拿到一张占位图。
#
# 但框架自己的用例需要一个**离线可跑**的供应商（不能真调外部厂商），所以
# 保留显式入口：测试调用它把示例驱动装进路由与驱动清单。
_EXAMPLE_DRIVERS = ("_mock_async", "_mock_sync")

# 模块名 → 驱动名。摘除时要按**驱动名**找路由里的注册项，而驱动名由驱动
# 自己声明（`name`），不一定等于文件名。
_EXAMPLE_PROVIDER_NAMES = {
    "_mock_async": "md_async",
    "_mock_sync": "md_sync",
}


def register_example_drivers() -> tuple[str, ...]:
    """显式装上示例驱动，返回它们的名字。

    幂等：已注册过的直接跳过，不重复报「驱动名已注册」。

    示例驱动会进模型目录，所以**只在测试与离线联调时调用**，生产路径不要碰。
    """
    ensure_drivers_registered()
    router = get_model_router()
    registered: list[str] = []
    for module_name in _EXAMPLE_DRIVERS:
        try:
            module = importlib.import_module(f"{_DRIVERS_PACKAGE}.{module_name}")
        except Exception as exc:  # noqa: BLE001 - 示例驱动缺失不该拖垮调用方
            message = f"{module_name}.py: {exc}"
            if message not in _errors:
                _errors.append(message)
            continue

        factory = getattr(module, "Driver", None)
        if factory is None:
            continue
        provider = _instantiate(factory)
        if provider.name not in router.provider_names():
            try:
                router.register(provider)
            except Exception as exc:  # noqa: BLE001 - 模型 id 撞车不能拖垮路由
                message = f"示例驱动 {provider.name!r} 注册失败：{exc}"
                if message not in _errors:
                    _errors.append(message)
                logger.error("示例驱动注册失败 %s", message)
                continue
            logger.info("已显式载入示例驱动 %s", provider.name)
        registered.append(provider.name)
        _remember_example(module_name, provider, module)
    return tuple(registered)


def _remember_example(module_name: str, provider: ModelProvider, module: Any) -> None:
    """把显式装上的示例驱动记进驱动清单。

    与 `_discover()` 的产物同构，所以 `driver_summaries()` / `app drivers list`
    照样能看到它们——只是这条路径**只有测试与离线联调**会走。
    """
    if _state is None:
        return
    meta = getattr(module, "META", None)
    if not isinstance(meta, standard.DriverMeta):
        meta = standard.DEFAULT_META
    summary = DriverSummary(
        name=provider.name,
        path=f"{_DRIVERS_PACKAGE.replace('.', '/')}/{module_name}.py",
        label=meta.label or provider.name,
        version=meta.version,
        capabilities=tuple(sorted(c.value for c in provider.capabilities)),
        model_count=len(provider.models),
        supports_poll=bool(getattr(provider, "supports_poll", False)),
        loaded=True,
    )
    _state[provider.name] = _LoadedDriver(provider=provider, summary=summary)


def unregister_example_drivers() -> tuple[str, ...]:
    """把显式装上的示例驱动摘干净，返回摘掉的名字。

    仅测试使用：路由是进程单例，示例驱动装上后必须能摘掉，否则会漏给
    后面的用例（那几条用例要断言「示例模型不在公开目录里」）。
    """
    router = get_model_router()
    removed: list[str] = []
    for module_name in _EXAMPLE_DRIVERS:
        provider = _EXAMPLE_PROVIDER_NAMES.get(module_name)
        if provider is None:
            continue
        if router.unregister(provider):
            removed.append(provider)
        if _state is not None:
            _state.pop(provider, None)
    return tuple(removed)


async def bootstrap() -> None:
    """`ModuleSpec.bootstrap`：任何实例启动时都确保驱动已接上路由。

    import 期已经做过一次；这里是给「路由单例被重建 / 子进程首次装配」
    兜底，`ensure_drivers_registered()` 本身幂等。
    """
    ensure_drivers_registered()
