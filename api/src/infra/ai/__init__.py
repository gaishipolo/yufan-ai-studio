"""模型供应商标注册与路由单例。

`AI_PROVIDERS` 逗号分隔（兼容旧的单值 `AI_PROVIDER`），每个名字对应
`infra.ai.<name>` 下的 `Driver`。

接入新厂商：
1. 新建 `infra/ai/volcengine.py`，实现 `submit` / `poll` / `cancel` 并声明 `models`
2. `.env` 加 `AI_PROVIDERS=echo,volcengine`
3. 完成 —— 业务层和后端核心都不需要改
"""

from __future__ import annotations

from functools import lru_cache

from core.config import settings
from core.logging import get_logger
from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
    ModelProvider,
    ModelSpec,
)
from infra.ai.router import ModelRouter
from infra.loader import load_driver

logger = get_logger(__name__)


@lru_cache
def get_model_router() -> ModelRouter:
    router = ModelRouter()
    for name in settings.ai_provider_list:
        provider: ModelProvider = load_driver("ai", name)
        router.register(provider)
        logger.info("已接入模型供应商 %s（%d 个模型）", provider.name, len(provider.models))

    if not router.provider_names():
        logger.warning("未配置任何模型供应商，生成类接口将不可用")
    return router


def reset_model_router() -> None:
    """仅测试使用。"""
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
