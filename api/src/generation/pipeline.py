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


@runtime_checkable
class CoverAttachable(Protocol):
    """可选能力：把库里派生好的封面写回业务记录。

    供应商多半不返回视频首帧（Agnes 只给 mp4 直链），而任务中心 / 分享 / 导出都靠
    `cover_url` 渲染卡片 —— 所以入库之后要有人把 ffmpeg 抽好的那一帧补回去。

    用协议而不是 `getattr(pipeline, "attach_cover", None)`：拼错的钩子名会让写回
    **静默不发生**，而症状是「卡片一直显示占位图」，没人会想到去查钩子名。
    """

    async def attach_cover(
        self, session: AsyncSession, record_id: str, cover_key: str
    ) -> bool: ...


@runtime_checkable
class DeadlineExtendable(Protocol):
    """可选能力：把墙钟预算的起点挪到「现在」。

    为什么单独一个协议：排队状态只对**异步长任务**有意义（同步供应商一次调用就出结果），
    强行塞进 `GenerationStore` 会逼着每个实现都写一个空方法，而空方法最容易
    写着写着就「看起来支持了」。用协议 + `isinstance` 探测，不支持就是真不支持
    （口径见 `docs/HANDOFF.md` §5.5：可选能力别用 getattr 探测）。
    """

    async def extend_deadline(self, record_id: str) -> bool:
        """顺延成功返回 True；记录不存在/已终态返回 False。"""
        ...


def strict_model(pipeline: GenerationPipeline) -> bool:
    """该流水线是否**禁止**「只有一个供应商就兜底」。未声明 = 允许。

    历史记录（轮询 / 重试 / 重启恢复）必须按当次记录的模型找回**原**供应商。
    模型解析不出来时，兜底会把任务打到另一家上：产物风格、单价、合规口径全变，
    日志里还看不出异常。模板替换属于这一类（配置里冻的是任务当时的渠道），
    所以它声明 `strict_model = True`。

    这是**声明式**的开关，不是自定义解析：怎么找供应商仍然只有 `ModelRouter`
    一处实现（见 `generation.runner._provider_for`）。
    """
    return bool(getattr(pipeline, "strict_model", False))


def fail_on_model_gone(pipeline: GenerationPipeline) -> bool:
    """模型从目录里消失时，轮询链路**当场判失败**还是**保留等人工**。

    默认 True（框架通用行为）：模型都没了，这条记录不可能再有结果，
    继续退避重试只会让用户等满墙钟预算、最后看到一句「生成超时」，
    完全指不出真正原因。

    替换链声明 False：它跑的是**已经付费**的任务（`task_id` 落在供应商侧），
    渠道停用只说明「我们联系不上供应商」，不代表任务失败。判失败会诱导用户
    去重试 → 重复计费。保留 `running` 等人工核对才是对的。
    """
    return bool(getattr(pipeline, "fail_on_model_gone", True))


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
