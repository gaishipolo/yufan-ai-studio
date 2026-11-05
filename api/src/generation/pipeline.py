"""生成流水线注册表。

一个「流水线」= 一种生成能力的业务适配器，回答三个问题：
1. 这类记录存在哪张表（`store`）
2. 记录怎么翻译成供应商请求（`build_request`）
3. 它属于哪种能力（`capability`）

新增一种生成能力（比如「视频配音」）：
    在你自己模块里
        @register_pipeline
        class VoicePipeline:
            kind = "voice"
            capability = Capability.AUDIO
            def store(self, session): return VoiceStore(session)
            async def build_request(self, record): ...

任务创建时 `dispatcher.dispatch("voice", record_id)` 就自动走上通用链路。
**不需要修改框架任何代码。**
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.registry import NamedRegistry, RegistryError
from generation.spec import GenerationRecord
from generation.store import GenerationStore
from infra.ai.base import Capability, GenerationRequest


@runtime_checkable
class GenerationPipeline(Protocol):
    kind: str
    capability: Capability

    def store(self, session: AsyncSession) -> GenerationStore: ...

    async def build_request(self, record: GenerationRecord) -> GenerationRequest: ...


pipelines = NamedRegistry[GenerationPipeline]("generation.pipeline")


def register_pipeline(pipeline: type[GenerationPipeline] | GenerationPipeline) -> object:
    """注册一个流水线。

    既可当类装饰器用（推荐）：

        @register_pipeline
        class VideoPipeline:
            kind = "video"
            ...

    也可直接传实例（需要注入依赖时）。注册表里统一存**实例**，
    否则 `pipeline.store(session)` 会把 session 误当成 self。
    """
    instance = pipeline() if isinstance(pipeline, type) else pipeline
    kind = getattr(instance, "kind", "")
    if not kind:
        raise RegistryError(f"{instance!r} 缺少 kind")
    pipelines.add(kind, instance)
    return pipeline


def get_pipeline(kind: str) -> GenerationPipeline:
    return pipelines.get(kind)


def pipeline_kinds() -> list[str]:
    return pipelines.names()
