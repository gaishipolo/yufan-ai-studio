"""模型路由单例与供应商协议类型。

供应商**不再**由 `AI_PROVIDERS` 白名单加载：`modules/model_driver/drivers/`
下的单文件驱动在模块发现期自动扫描注册（见 `modules/model_driver/registry.py`）。
接入新厂商只需在 `drivers/` 放一个 py 文件（META + MODELS + Driver），
不改 `.env`、不改这里的 import。

这里只持有单例并导出协议类型。注册动作归 `ensure_drivers_registered()`：
它需要拿到这个 router 才能往里写，若本函数反过来调它，会在 `lru_cache`
未填充时形成无限递归。测试里 `reset_model_router()` 换掉单例后，
下一次 `ensure_drivers_registered()` 会自愈。
"""

from __future__ import annotations

from functools import lru_cache

from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
    ModelProvider,
    ModelSpec,
)
from infra.ai.router import ModelRouter


@lru_cache
def get_model_router() -> ModelRouter:
    """进程单例。故意只构造、不注册：注册由 `ensure_drivers_registered()` 完成。"""
    return ModelRouter()


def reset_model_router() -> None:
    """仅测试使用。调用方随后要跑 `ensure_drivers_registered()` 重新装满路由。"""
    get_model_router.cache_clear()


__all__ = [
    "Capability",
    "GenerationProgress",
    "GenerationRequest",
    "GenerationState",
    "ModelProvider",
    "ModelSpec",
    "ModelRouter",
    "get_model_router",
    "reset_model_router",
]
