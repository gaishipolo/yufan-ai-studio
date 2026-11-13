"""驱动私有配置的基座。

**框架只放「部署策略」，驱动自己放「实现参数」**：

- 全局 `core.config.Settings`：用哪个驱动、worker 并发、鉴权、数据库…（所有驱动共有）
- `DriverSettings` 子类：某个 provider 的 base_url / api_key、某个存储的根目录…

约定（新增驱动时照抄）：

1. 环境变量前缀 = 驱动名大写 + `_`，例如 `AGNES_BASE_URL`、`LIBTV_NODE`；
2. 每个驱动一个 `XxxSettings` + 一个 `@lru_cache` 的 `get_xxx_settings()`；
3. 与全局 `Settings` **共用同一个 `.env`**，靠 `extra="ignore"` 保证互不干扰；
4. 字段必须有安全的默认值，或在使用处显式报错 —— 不允许默认指向某台机器的盘符。

为什么不用「一个模块一个 config.py」：业务模块的参数多半是**产品规则**
（分辨率、提示词模板），它们该进数据库或代码常量，而不是环境变量。
真正需要环境变量的只有「部署时才确定」的东西，也就是驱动参数。
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict

from core.config import ENV_FILE

_INFRA_ROOT = Path(__file__).resolve().parent

# 显式声明过 model_config 的子类会登记在这里，供 `tests/test_config_guard.py` 巡检。
# 延迟导入的驱动只有被 import 时才会登记，因此测试里要显式 import 待检查的模块。
_REGISTRY: list[type[DriverSettings]] = []


def driver_config(prefix: str) -> SettingsConfigDict:
    """生成子类的 `model_config`，避免每个驱动抄一遍五行配置。"""
    return SettingsConfigDict(
        env_prefix=f"{prefix}_",
        env_file=(ENV_FILE,),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


class DriverSettings(BaseSettings):
    """驱动的私有配置。子类只需覆写 `model_config` 指定前缀。

    两个 `ClassVar` 是**配置自描述**：让消费方按「这个配置声明自己负责什么」
    来筛选，而不是遍历全局注册表按字段名 `timeout_sec` 猜语义（R12-03）。
    新驱动接进来时，不声明就等于不参与任何预算推导、不要求任何强制项。
    """

    model_config = SettingsConfigDict(
        env_file=(ENV_FILE,),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 这个配置贡献哪些能力的「单次提交耗时预算」（能力名用 Capability 的 value）。
    # 只有真正调用供应商、会占住 worker 的那次提交才该声明；下载超时不算。
    submit_budget_for: ClassVar[frozenset[str]] = frozenset()
    # 生产环境必须显式配置的字段。留空表示允许走代码里的安全兜底。
    prod_required_fields: ClassVar[tuple[str, ...]] = ()
    # 这个配置服务于哪种驱动（storage / archive / queue / ai）以及驱动名。
    # 留空 = 只要 import 就参与生产必填检查（AI 供应商都是这种）。
    # 有**替代实现**的驱动必须声明：用 local 部署时不该因为没配 S3_ENDPOINT 而起不来。
    driver_kind: ClassVar[str] = ""
    driver_name: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # 只登记自己声明了前缀的子类；基类本身与中间抽象类不进巡检范围
        if "model_config" in cls.__dict__:
            _REGISTRY.append(cls)


def registered_driver_settings() -> tuple[type[DriverSettings], ...]:
    """已登记的驱动配置类（按 import 顺序）。"""
    return tuple(dict.fromkeys(_REGISTRY))


def import_all_driver_settings(
    *, extra_modules: Sequence[str] = ()
) -> tuple[type[DriverSettings], ...]:
    """import 全部 infra 驱动模块，返回其中的 `DriverSettings` 子类。

    给**配置巡检**用：`.env.example` 要求「写了就必须能被某个 Settings 解析」，
    而巡检必须看到所有可能的驱动配置，不能只看到当前启用的那几个。

    为什么不用 `pkgutil.walk_packages`：`infra/storage/` 与 `infra/queue/` 是
    namespace package（没有 `__init__.py`），`walk_packages` 不递归进去，于是这两个
    驱动目录整个被漏掉——守卫会「通过」，但 `S3Settings` 从来没被查过。这里直接
    扫文件系统，不依赖包结构。

    为什么从模块对象取类、而不是读 `_REGISTRY`：登记是 import 的副作用，而
    `sys.modules` 里的模块不会重新执行。`test_prod_guard_imports_active_driver_before_checking`
    会把 `_REGISTRY` 和 `sys.modules["infra.storage.s3"]` 一起清空再触发重新
    import，teardown 时两者各自恢复——重新执行产生的类落进了临时列表，而模块缓存
    恢复成旧对象，结果是登记表**永久**少一个类（其它用例开始误报「未知配置键」）。
    直接读 `vars(module)` 不受 import 时序影响。

    `extra_modules` 用于业务模块自带的驱动配置（例如 `modules.image.pipeline`
    的 `ImageAssetSettings`）：它们不在 `infra/` 下，但同样要进巡检。
    """
    found: list[type[DriverSettings]] = []

    def collect(module: object) -> None:
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, DriverSettings)
                and value is not DriverSettings
            ):
                found.append(value)

    for path in _INFRA_ROOT.rglob("*.py"):
        if path.name == "__init__.py" or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(_INFRA_ROOT).with_suffix("")
        module_name = "infra." + ".".join(relative.parts)
        try:
            collect(importlib.import_module(module_name))
        except ModuleNotFoundError as exc:
            # 可选依赖缺失（例如没装 boto3）不该让配置巡检整体失败，
            # 但「模块本身不存在」以外的错误必须冒出来。
            if exc.name == module_name:
                continue
            raise
    for module_name in extra_modules:
        collect(importlib.import_module(module_name))
    return tuple(dict.fromkeys(found))


def registered_submit_budgets(capability: str) -> list[float]:
    """只取**显式声明**贡献该能力提交预算的驱动配置的 `timeout_sec`。

    这里刻意不吞构造异常、不 `continue`：某个驱动声明了预算却没配好必填项，
    悄悄跳过会让阈值回落到 90 秒，正好重新打开「巡检误伤在途任务」的窗口
    （R11-02 / R12-03）。配置坏了就该在巡检或启动时响亮地失败。
    """
    budgets: list[float] = []
    for cls in registered_driver_settings():
        if capability not in cls.submit_budget_for:
            continue
        value = getattr(cls(), "timeout_sec", None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError(
                f"{cls.__name__} 声明了 {capability} 的提交预算，"
                "却没有有效的 timeout_sec 字段"
            )
        budgets.append(float(value))
    return budgets


def _driver_is_active(cls: type[DriverSettings]) -> bool:
    """未启用的替代实现不参与生产必填检查。

    `S3Settings` 与本地存储是同一个 kind 的两个实现：用 local 部署时要求
    `S3_ENDPOINT`，只会让所有人生不如死。声明了 `driver_kind` 的配置才受这条约束，
    其它（AI 供应商）保持「import 即检查」的原有语义。
    """
    kind = cls.driver_kind
    if not kind:
        return True

    from core.config import settings

    if kind == "storage":
        return settings.storage_driver == cls.driver_name
    if kind == "archive":
        return settings.archive_driver == cls.driver_name
    if kind == "queue":
        return settings.queue_driver == cls.driver_name
    if kind == "cache":
        return settings.cache_driver == cls.driver_name
    # 模型驱动不再有「启用清单」：drivers/ 下的单文件驱动全部自动载入，
    # 所以它们的 Settings 全部纳入启动巡检（没配密钥要早报，不等第一次提交）。
    return True


def _ensure_active_drivers_imported() -> None:
    """把**当前启用**的驱动模块 import 进来，让它们的 Settings 完成登记。

    没有这一步，生产巡检就是「import 过才查」：`STORAGE_DRIVER=s3` 却漏配
    `S3_SECRET_KEY` 时，`infra.storage.s3` 要等第一次上传才被 `load_driver`
    加载——于是服务正常启动、健康检查通过、第一张素材上传时才 502。

    只 import 启用的那个：用 local 部署的机器不该为了巡检去 import boto3。
    """
    from core.config import settings

    targets = [
        ("storage", settings.storage_driver),
        ("archive", settings.archive_driver),
        ("queue", settings.queue_driver),
        ("cache", settings.cache_driver),
    ]
    # 模型驱动不在这里按名字 import：`modules/model_driver/registry.py` 扫描
    # `drivers/` 时已经把每个文件加载过一遍，各自的 Settings 也就登记完了。

    for kind, name in targets:
        module = f"infra.{kind}.{name}"
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            # 驱动本身没实现时由 load_driver 给出更好的报错，这里不抢先失败
            if exc.name == module:
                continue
            raise


def ensure_prod_driver_settings(app_env: str) -> None:
    """启动自检：生产环境不许依赖代码里的兜底策略（R12-04 / R11-05 同一口径）。

    `record()` 与成图下载校验都按设计「失败时静默退回」，所以没配上白名单
    这件事在 dev/test 里是无害的；生产里它等于把 SSRF 防线交给黑名单兜底，
    而且日志里一句错都没有。必须在启动期炸掉。
    """
    if app_env != "prod":
        return
    _ensure_active_drivers_imported()
    missing: list[str] = []
    for cls in registered_driver_settings():
        if not _driver_is_active(cls):
            continue
        instance = cls()
        for field in cls.prod_required_fields:
            value = getattr(instance, field, None)
            if value is None or not str(value).strip():
                missing.append(f"{cls.__name__}.{field}")
    if missing:
        raise RuntimeError(
            "生产环境驱动配置缺失（不允许使用代码兜底）：" + ", ".join(missing)
        )


def precheck_submit_budgets() -> dict[str, list[float]]:
    """启动预检：把每个被声明的「提交预算」在启动期算一遍。

    `registered_submit_budgets()` 对坏配置是**抛异常**而不是静默跳过（R12-03），
    但它的消费方是周期任务，`Scheduler.run_once` 捕获后只记一条日志就 `continue`：
    配置坏掉时巡检永久停摆，每 300 秒刷一条错，谁也不会盯着日志等它被发现
    （R13-06）。在启动期先算一次，坏配置变成一次说清楚「哪个类哪个字段」的
    快速失败——这一条不区分环境，因为「巡检再也不会跑」在任何环境都是错。

    返回 `{能力名: 预算列表}`，供启动日志展示；不做任何 IO。
    """
    # 注册表是 import 副作用：先按当前配置把**启用中**的驱动 import 进来
    # （不扫全包，只 import 部署实际用到的那几个），否则 dev 环境下什么都没登记，
    # 这个预检会安静地返回空字典——比不检查更糟（R13-05 的另一面）。
    _ensure_active_drivers_imported()
    capabilities = sorted(
        {capability for cls in registered_driver_settings() for capability in cls.submit_budget_for}
    )
    return {capability: registered_submit_budgets(capability) for capability in capabilities}


__all__ = [
    "DriverSettings",
    "driver_config",
    "ensure_prod_driver_settings",
    "import_all_driver_settings",
    "precheck_submit_budgets",
    "registered_driver_settings",
    "registered_submit_budgets",
]
