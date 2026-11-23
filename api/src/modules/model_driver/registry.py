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
import importlib.util
import pkgutil
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from core.logging import get_logger
from core.ports.model_generation import DriverSummary
from infra import get_model_router
from infra.ai.base import ModelProvider, PollCapable
from infra.ai.router import ModelRouter
from modules.model_driver import spec as standard

logger = get_logger(__name__)

_DRIVERS_PACKAGE = "modules.model_driver.drivers"


@dataclass(frozen=True, slots=True)
class _LoadedDriver:
    provider: ModelProvider
    summary: DriverSummary
    #: 驱动自报的接口面。框架公共层不认识供应商，所以它们由驱动文件自己
    #: 声明（`EXTRA_ROUTES` / `EXTRA_JOBS` / `EXTRA_SCHEMAS`），这里只负责收集。
    extra_routes: tuple[APIRouter, ...] = ()
    extra_jobs: tuple[standard.JobSpec, ...] = ()
    extra_schemas: tuple[type[BaseModel], ...] = ()


_state: dict[str, _LoadedDriver] | None = None
_registered: set[str] = set()
_errors: list[str] = []

#: 已经挂过的驱动路由（按对象身份）。`include_router` 是**复制**路由，
#: 挂两次就是同一批端点出现两遍——所以按身份去重，而不是按名字。
_mounted_routers: list[APIRouter] = []

#: 已成功加载的驱动模块 → 该文件当时的指纹 `(mtime_ns, size)`。
#:
#: 刷新靠它区分两种情况：**文件是新增的**（可热接上）与**文件已经加载过**
#: （改过内容也不热替换，只记进 `changed_files` 提醒重启）。
#: 加载**失败**的文件不登记 —— 下次刷新会重试它。
_imported_paths: dict[str, tuple[int, int]] = {}


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """一次驱动刷新装配的结果。

    这是给运维看的，不是给机器解析的：每一项都对应一个具体动作
    （哪些热生效了、哪些要重启、哪里坏了）。
    """

    #: 本次**热生效**的新驱动（对应新增的文件）。
    added: tuple[str, ...] = ()
    #: 已在运行、保持原实例的驱动（没被换掉）。
    kept: tuple[str, ...] = ()
    #: 文件被改过但**没有**热生效的驱动 —— 要重启才生效。
    changed_files: tuple[str, ...] = ()
    #: 声明了作业却没有 handler 的作业名（运行期只能报出来）。
    missing_handlers: tuple[str, ...] = ()
    #: 本次刷新发现的错误：加载失败、注册失败、巡检失败。
    errors: tuple[str, ...] = ()
    #: 谁触发的刷新（端点传入的用户 id；周期任务为空）。
    operator: str = ""
    #: 刷新时刻（ISO8601）。
    refreshed_at: str = ""


#: 自描述装配失败。定义在 `spec.py` —— 那是自描述契约该待的地方；这里只是
#: 便于本模块内引用，不另立一个名字。
ExtrasError = standard.ExtrasError


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


def _read_extra_routes(module: Any) -> tuple[APIRouter, ...]:
    raw = getattr(module, "EXTRA_ROUTES", ())
    if not isinstance(raw, tuple):
        raise ExtrasError("EXTRA_ROUTES 必须是 tuple[APIRouter, ...]")
    for item in raw:
        if not isinstance(item, APIRouter):
            raise ExtrasError(
                f"EXTRA_ROUTES 里必须是 APIRouter，收到 {type(item).__name__}"
            )
    return raw


def _read_extra_jobs(module: Any) -> tuple[standard.JobSpec, ...]:
    raw = getattr(module, "EXTRA_JOBS", ())
    if not isinstance(raw, tuple):
        raise ExtrasError("EXTRA_JOBS 必须是 tuple[JobSpec, ...]")
    for item in raw:
        if not isinstance(item, standard.JobSpec):
            raise ExtrasError(
                f"EXTRA_JOBS 里必须是 JobSpec，收到 {type(item).__name__}"
            )
    return raw


def _read_extra_schemas(module: Any) -> tuple[type[BaseModel], ...]:
    raw = getattr(module, "EXTRA_SCHEMAS", ())
    if not isinstance(raw, tuple):
        raise ExtrasError("EXTRA_SCHEMAS 必须是 tuple[type[BaseModel], ...]")
    for item in raw:
        if not (isinstance(item, type) and issubclass(item, BaseModel)):
            raise ExtrasError(
                f"EXTRA_SCHEMAS 里必须是 BaseModel 子类，收到 {type(item).__name__}"
            )
    return raw


def _module_stamp(module_path: str) -> tuple[int, int] | None:
    """模块源文件的指纹 `(mtime_ns, size)`；拿不到就是 `None`。

    用它回答一个问题：**这个文件的内容变过吗？** 变了也不热替换
    （`importlib.reload` 会丢 in-flight 实例状态），只用来提醒运维重启。
    """
    try:
        spec = importlib.util.find_spec(module_path)
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin:
        return None
    try:
        stat = Path(spec.origin).stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _load_module(file_name: str, module_path: str) -> _LoadedDriver:
    """import 并校验单个驱动文件。

    失败抛异常，由调用方决定「记一条」还是「炸掉」。接口面装配失败抛
    `ExtrasError`（必须炸），其余是普通的加载失败（只影响这一个驱动）。
    """
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
        supports_poll=isinstance(provider, PollCapable) and bool(provider.supports_poll),
        loaded=True,
    )
    return _LoadedDriver(
        provider=provider,
        summary=summary,
        extra_routes=_read_extra_routes(module),
        extra_jobs=_read_extra_jobs(module),
        extra_schemas=_read_extra_schemas(module),
    )


def _mount_extra_routes(drivers: dict[str, _LoadedDriver]) -> None:
    """把驱动自报的端点挂到模块 router 上。

    纪律：**挂载失败必须启动失败**。端点凭空消失 = 前端 404 + 契约少一块，
    而没人会想到问题出在驱动装配上。

    挂载不重复：`include_router` 是复制路由，同一个 router 挂两次就是同一批
    端点出现两遍，所以按对象身份去重（`reset_drivers()` 后重新扫描也是同一批
    对象，不会重复挂）。
    """
    from modules.model_driver.router import router as module_router

    for name, item in drivers.items():
        for extra in item.extra_routes:
            if any(extra is mounted for mounted in _mounted_routers):
                continue
            try:
                module_router.include_router(extra)
            except Exception as exc:  # noqa: BLE001 - 转成启动失败，不是静默少端点
                raise ExtrasError(
                    f"驱动 {name!r} 的 EXTRA_ROUTES 挂载失败：{exc}"
                ) from exc
            _mounted_routers.append(extra)
            logger.info(
                "已挂载驱动 %s 自报的 %d 条路由", name, len(extra.routes)
            )


def _failed_driver(file_name: str, error: str) -> _LoadedDriver:
    """加载失败的驱动在清单里的占位（`loaded=False` + 错误原因）。"""
    return _LoadedDriver(
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
            error=error,
        ),
    )


def _discover() -> None:
    """扫描并校验所有驱动文件。单个文件出错只记一条，不中断其他驱动。"""
    global _state
    loaded: dict[str, _LoadedDriver] = {}
    for file_name, module_path in _iter_driver_modules():
        try:
            item = _load_module(file_name, module_path)
        except ExtrasError:
            # 接口面装配失败不吞：端点静默消失比启动失败难查得多。
            raise
        except Exception as exc:  # noqa: BLE001 - 第三方驱动文件不该让整个服务起不来
            message = f"{file_name}.py: {exc}"
            _errors.append(message)
            logger.error("模型驱动加载失败 %s", message)
            loaded[f"__failed__{file_name}"] = _failed_driver(file_name, str(exc))
            continue
        if item.provider.name in loaded:
            message = f"驱动名 {item.provider.name!r} 与 {file_name}.py 之前的驱动重复"
            _errors.append(message)
            logger.error("模型驱动加载失败 %s", message)
            loaded[f"__failed__{file_name}"] = _failed_driver(file_name, message)
            continue
        loaded[item.provider.name] = item
        # 只有**成功加载**的才登记：加载失败的文件下次刷新还会重试。
        _imported_paths[module_path] = _module_stamp(module_path) or (0, 0)
    _state = loaded
    # 接口面装配放在最后：驱动都载入了再挂路由；挂了不上就是启动失败。
    _mount_extra_routes(loaded)


def _register_loaded(state: dict[str, _LoadedDriver], router: ModelRouter) -> None:
    """把已加载的驱动补进 `ModelRouter`。可重复调用，路由被重置后能自愈。

    与扫描分开，是为了让 `refresh()` 能**只做装配**不触发重新扫描 ——
    重新扫描会把「新增文件」当成启动期文件加载掉，热接上就无从谈起。
    """
    existing = set(router.provider_names())
    for name, item in state.items():
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
                if resolved is not None:
                    state[name] = replace(item, provider=resolved)
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
            state[name] = replace(
                item, summary=replace(item.summary, loaded=False, error=str(exc))
            )
            logger.error("模型驱动注册失败 %s", message)
        else:
            _registered.add(name)
            logger.info(
                "已自动载入模型驱动 %s（%d 个模型）", name, item.summary.model_count
            )


def ensure_drivers_registered() -> None:
    """把已发现的驱动补进 `ModelRouter`。可重复调用，路由被重置后能自愈。"""
    if _state is None:
        _discover()

    _register_loaded(_state or {}, get_model_router())


def driver_summaries() -> tuple[DriverSummary, ...]:
    """驱动清单（含加载失败项），按名字排序。"""
    ensure_drivers_registered()
    items = [item.summary for item in (_state or {}).values()]
    return tuple(sorted(items, key=lambda item: item.name))


def load_errors() -> tuple[str, ...]:
    ensure_drivers_registered()
    return tuple(_errors)


def job_names() -> tuple[str, ...]:
    """所有驱动自报的作业名。

    框架按这份清单核对「声明了就必须有 handler」：声明了却没注册 handler，
    启动就失败（而不是静默少一个消费者）。所以这份清单由驱动自己给，
    公共层不写死任何供应商的作业名。
    """
    ensure_drivers_registered()
    names: list[str] = []
    for item in (_state or {}).values():
        for job in item.extra_jobs:
            if job.name not in names:
                names.append(job.name)
    return tuple(names)


def extra_schemas() -> tuple[type[BaseModel], ...]:
    """所有驱动自报的出参 schema（供应商专属那部分）。

    公共层的出入参不在这里——它们是框架自己的口径，由 `schema.py` 定义。
    """
    ensure_drivers_registered()
    seen: dict[str, type[BaseModel]] = {}
    for item in (_state or {}).values():
        for schema in item.extra_schemas:
            seen.setdefault(f"{schema.__module__}.{schema.__qualname__}", schema)
    return tuple(seen.values())


def _missing_handlers(item: _LoadedDriver) -> tuple[str, ...]:
    """声明了作业却没有 handler 的作业名。

    handler 是 **import 期**装饰器（`workers.register_handler`），所以驱动模块被
    import 时就已经注册好了。启动期有「声明了就必须有 handler」的核对并在缺
    handler 时让服务起不来；运行期新增的驱动没有「启动失败」这个选项了，
    只能**报出来** —— 少一个消费者的话，作业会一直停在 queued。
    """
    from workers import handler_names  # 延迟导入：避免 import 期循环

    registered = set(handler_names())
    return tuple(job.name for job in item.extra_jobs if job.name not in registered)


def _recheck_runtime_config() -> tuple[str, ...]:
    """刷新时重跑必填项与提交预算预检。

    不抛：运行期没有「启动失败」这个选项，报告出来比炸掉更可用。但问题会
    同时进日志与刷新结果 —— 配置被改坏这件事必须**立刻**被发现，而不是
    等下次重启（那时坏配置已经生效过一整轮了）。
    """
    from core.config import get_settings
    from infra.driver_config import ensure_prod_driver_settings, precheck_submit_budgets

    problems: list[str] = []
    try:
        ensure_prod_driver_settings(get_settings().app_env)
    except Exception as exc:  # noqa: BLE001 - 报告问题，不让刷新整体失败
        problems.append(f"必填项巡检失败：{exc}")
    try:
        precheck_submit_budgets()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"提交预算预检失败：{exc}")
    for problem in problems:
        logger.error("驱动刷新时巡检未通过 %s", problem)
    return tuple(problems)


def refresh(*, operator: str = "") -> RefreshResult:
    """重新扫描驱动目录，把**新增**的驱动文件热接上。

    ## 边界（这几条是刻意选的，不是没做完）

    - **加文件 = 热生效**：新文件会被 import、校验、注册，供应商专属端点也会
      挂上（不用重启）。
    - **改已加载的文件 = 需重启**：`importlib.reload` 会丢 in-flight 实例状态
      （正在跑的请求、已提交的 `external_id` 指向的旧类），收益不抵风险。
      改了内容只会出现在 `changed_files` 里 —— 提醒重启，不假装生效。
    - **刷新是进程级的**：多实例部署下只影响本进程；其他实例靠各自的周期
      刷新收敛（周期任务 `leader_only=False`，每实例都跑）。
    - **已注册的驱动不会被换掉**：同名且模型清单一致时复用路由里那份实例。

    ## 为什么巡检失败不抛

    运行期没有「启动失败」这个选项。坏配置必须立刻暴露（进日志、进结果、
    运维看得见），但把整个刷新打回去会让「新增驱动已经接上了」这件事也
    看不到 —— 两件事要分开报。
    """
    global _state

    if _state is None:
        # 还没扫描过（少见：进程刚起还没 ensure）→ 先走一次全量扫描，
        # 否则刷新后 `_state` 里只剩新加载的驱动，把原有的弄丢了。
        _discover()

    router = get_model_router()
    before = set(router.provider_names())

    state = dict(_state or {})
    changed: list[str] = []
    errors: list[str] = []
    missing_handlers: list[str] = []

    for file_name, module_path in _iter_driver_modules():
        stamp = _module_stamp(module_path)
        known = _imported_paths.get(module_path)
        if known is not None:
            # 已经加载过：**不热替换**（reload 会丢 in-flight 实例状态）。
            if stamp is not None and stamp != known:
                changed.append(file_name)
            continue
        try:
            item = _load_module(file_name, module_path)
        except ExtrasError:
            # 接口面装配失败必须炸：端点凭空消失比启动失败难查得多。
            raise
        except Exception as exc:  # noqa: BLE001 - 单个驱动文件出错不该拖垮整个刷新
            message = f"{file_name}.py: {exc}"
            errors.append(message)
            logger.error("刷新时驱动加载失败 %s", message)
            continue
        if item.provider.name in state:
            message = f"驱动名 {item.provider.name!r} 重复（{file_name}.py）"
            errors.append(message)
            logger.error("刷新时驱动加载失败 %s", message)
            continue
        state[item.provider.name] = item
        _imported_paths[module_path] = stamp or (0, 0)
        missing_handlers.extend(_missing_handlers(item))

    _state = state
    # 路由侧：把新驱动接进路由。装配是幂等的，且同名且模型清单一致时会复用
    # 路由里那份实例（不换掉正在跑的实例）。
    _register_loaded(state, router)
    after = set(router.provider_names())
    added = tuple(sorted(after - before))
    kept = tuple(sorted(after & before))
    # 端点挂载：新驱动的 EXTRA_ROUTES 挂上（已挂过的按对象身份跳过）。
    _mount_extra_routes(state)

    errors.extend(_recheck_runtime_config())

    refreshed_at = datetime.now(UTC).isoformat(timespec="seconds")
    logger.info(
        "驱动刷新完成：操作人=%s，新增=%s，保留=%s，变更待重启=%s，错误=%d",
        operator or "（周期任务）",
        list(added),
        list(kept),
        changed,
        len(errors),
    )
    return RefreshResult(
        added=added,
        kept=kept,
        changed_files=tuple(changed),
        missing_handlers=tuple(missing_handlers),
        errors=tuple(errors),
        operator=operator,
        refreshed_at=refreshed_at,
    )


def reset_drivers() -> None:
    """仅测试使用：清掉发现结果，下次调用会重新扫描目录。

    不碰 `ModelRouter` 的单例——它归 `infra.ai.reset_model_router()` 管。
    """
    global _state
    _state = None
    _registered.clear()
    _errors.clear()
    # 已加载记录也要清：否则刷新会以为这些文件「已经加载过」，新写的驱动
    # 文件永远接不上（`reset_drivers()` 的语义就是「回到没扫过的状态」）。
    _imported_paths.clear()


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
        supports_poll=isinstance(provider, PollCapable) and bool(provider.supports_poll),
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
