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
import pkgutil
from typing import Any, ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict

from core.logging import get_logger

logger = get_logger(__name__)

# 显式声明过 model_config 的子类会登记在这里，供 `tests/test_config_guard.py` 巡检。
# 延迟导入的驱动只有被 import 时才会登记，因此测试里要显式 import 待检查的模块。
_REGISTRY: list[type[DriverSettings]] = []

# 驱动实际所在的三类包（与 `infra.<kind>.<name>` 的命名约定一致）
_DRIVER_PACKAGES = ("infra.ai", "infra.archive", "infra.storage")


def driver_config(prefix: str) -> SettingsConfigDict:
    """生成子类的 `model_config`，避免每个驱动抄一遍五行配置。"""
    return SettingsConfigDict(
        env_prefix=f"{prefix}_",
        env_file=(".env",),
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
        env_file=(".env",),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 这个配置贡献哪些能力的「单次提交耗时预算」（能力名用 Capability 的 value）。
    # 只有真正调用供应商、会占住 worker 的那次提交才该声明；下载超时不算。
    submit_budget_for: ClassVar[frozenset[str]] = frozenset()
    # 生产环境必须显式配置的字段。留空表示允许走代码里的安全兜底。
    prod_required_fields: ClassVar[tuple[str, ...]] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # 只登记自己声明了前缀的子类；基类本身与中间抽象类不进巡检范围
        if "model_config" in cls.__dict__:
            _REGISTRY.append(cls)


def registered_driver_settings() -> tuple[type[DriverSettings], ...]:
    """已登记的驱动配置类（按 import 顺序）。"""
    return tuple(dict.fromkeys(_REGISTRY))


def discover_driver_settings() -> tuple[type[DriverSettings], ...]:
    """把各驱动包里的模块都 import 一遍，让配置类登记齐全。

    注册表是「被 import 时追加」的副作用，于是**检查范围取决于谁先碰巧
    import 了驱动**：只加载 echo 的部署里 `AgnesSettings` 根本不在册，
    `ensure_prod_driver_settings()` 扫不到它，将来给它加 `prod_required_fields`
    也等于没加（R13-05）。`maintenance._stale_after_sec()` 曾靠函数内
    `from infra.ai import agnes` 兜住，那是把「注册齐全」当成了巧合。

    这里按包目录扫描，**不写死驱动名**：新增驱动文件即自动纳入巡检，
    与 `infra/loader.py` 的「加文件不改代码」保持一致。
    单个模块导入失败只记 warning 并继续——缺某个驱动不该让整个应用起不来，
    但必须留痕，否则又是一次「巡检范围悄悄变小」。
    """
    for package_name in _DRIVER_PACKAGES:
        try:
            package = importlib.import_module(package_name)
        except Exception as exc:  # noqa: BLE001 - 包本身导不进来时不能连坐其余包
            logger.warning("驱动包 %s 导入失败：%s（其配置声明不参与巡检）", package_name, exc)
            continue
        for info in pkgutil.iter_modules(package.__path__):
            if info.ispkg or info.name.startswith("_"):
                continue
            module_name = f"{package_name}.{info.name}"
            try:
                importlib.import_module(module_name)
            except Exception as exc:  # noqa: BLE001 - 同理，逐个模块隔离
                logger.warning(
                    "驱动模块 %s 导入失败：%s（其配置声明不参与巡检）", module_name, exc
                )
    return registered_driver_settings()


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


def ensure_prod_driver_settings(app_env: str) -> None:
    """启动自检：生产环境不许依赖代码里的兜底策略（R12-04 / R11-05 同一口径）。

    `record()` 与成图下载校验都按设计「失败时静默退回」，所以没配上白名单
    这件事在 dev/test 里是无害的；生产里它等于把 SSRF 防线交给黑名单兜底，
    而且日志里一句错都没有。必须在启动期炸掉。
    """
    if app_env != "prod":
        return
    missing: list[str] = []
    for cls in registered_driver_settings():
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
    capabilities = sorted(
        {capability for cls in registered_driver_settings() for capability in cls.submit_budget_for}
    )
    return {capability: registered_submit_budgets(capability) for capability in capabilities}


__all__ = [
    "DriverSettings",
    "discover_driver_settings",
    "driver_config",
    "ensure_prod_driver_settings",
    "precheck_submit_budgets",
    "registered_driver_settings",
    "registered_submit_budgets",
]
