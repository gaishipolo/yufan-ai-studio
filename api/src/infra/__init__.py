"""基础设施工厂：所有可替换实现都从这里获取。

业务层通过 `Depends(get_storage)` 之类的依赖注入拿到**协议**，
而不是具体类 —— 这是整个项目最主要的扩展点。

驱动由 `infra/loader.py` 按名字动态解析，本文件**不再包含任何 if/elif**。
新增驱动只需新增文件 + 改配置，工厂代码保持不动。
"""

from __future__ import annotations

from functools import lru_cache

from core.config import settings
from infra.ai import GenerationRequest, GenerationState, ModelProvider, get_model_router
from infra.ai.base import Capability
from infra.loader import load_driver
from infra.queue.base import Job, JobQueue
from infra.storage.base import ObjectStorage


@lru_cache
def get_storage() -> ObjectStorage:
    return load_driver("storage", settings.storage_driver)


@lru_cache
def get_queue() -> JobQueue:
    return load_driver("queue", settings.queue_driver)


def get_video_provider() -> ModelProvider:
    """取默认视频模型的供应商（兼容旧的单 provider 注入点）。"""
    router = get_model_router()
    return router.provider_for(router.default_model(Capability.VIDEO))


def get_image_provider() -> ModelProvider:
    router = get_model_router()
    return router.provider_for(router.default_model(Capability.IMAGE))


def reset_infra_cache() -> None:
    """测试用：清空工厂缓存以便切换配置。"""
    from infra.archive import reset_archive_storage

    for factory in (get_storage, get_queue):
        factory.cache_clear()
    reset_archive_storage()


__all__ = [
    "Capability",
    "GenerationRequest",
    "GenerationState",
    "Job",
    "JobQueue",
    "ObjectStorage",
    "get_image_provider",
    "get_model_router",
    "get_queue",
    "get_storage",
    "get_video_provider",
    "reset_infra_cache",
]
