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
            # AI_PROVIDERS 配置的同名驱动。按**模型清单**区分：一致就是同一个
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
                f"路由已有 {sorted(routed)}）：请改名或核对 AI_PROVIDERS"
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


async def bootstrap() -> None:
    """`ModuleSpec.bootstrap`：任何实例启动时都确保驱动已接上路由。

    import 期已经做过一次；这里是给「路由单例被重建 / 子进程首次装配」
    兜底，`ensure_drivers_registered()` 本身幂等。
    """
    ensure_drivers_registered()
