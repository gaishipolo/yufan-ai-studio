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

from typing import Any, ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict

# 显式声明过 model_config 的子类会登记在这里，供 `tests/test_config_guard.py` 巡检。
# 延迟导入的驱动只有被 import 时才会登记，因此测试里要显式 import 待检查的模块。
_REGISTRY: list[type[DriverSettings]] = []


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


__all__ = [
    "DriverSettings",
    "driver_config",
    "ensure_prod_driver_settings",
    "registered_driver_settings",
    "registered_submit_budgets",
]
