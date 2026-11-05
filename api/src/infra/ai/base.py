"""AI 模型供应商抽象。

面向「能力」而不是「厂商」：视频、图片、音频都是同一种生成范式
（提交 → 轮询 → 终态），差异只在参数和返回结构。

于是整个项目只保留**一个** provider 协议。接火山、可灵、通义，
都只是实现这个协议并声明自己的模型清单，业务层完全无感。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Capability(StrEnum):
    """生成能力。新增能力（如 3D、配音）只需在此加一项。"""

    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"


class GenerationState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in (GenerationState.SUCCEEDED, GenerationState.FAILED)


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """提交给供应商的请求。`params` 承载能力相关的自由字段。"""

    capability: Capability
    model: str
    prompt: str
    params: Mapping[str, Any] = field(default_factory=dict)
    reference_urls: tuple[str, ...] = ()
    # 业务侧记录 ID，便于供应商账单/日志对账；供应商可忽略
    request_id: str = ""


@dataclass(frozen=True, slots=True)
class GenerationProgress:
    """供应商返回的一次状态快照。"""

    external_id: str
    state: GenerationState
    progress: int = 0
    output_url: str | None = None
    cover_url: str | None = None
    error: str | None = None
    # 供应商明确告知「可重试」（限流、临时故障）时为 True，触达重试策略
    retryable: bool = False

    def __post_init__(self) -> None:
        """在 provider 边界把状态归一成枚举。

        dataclass 不做类型校验，第三方实现很容易直接写 `state="succeeded"`；
        一旦下游用 `is GenerationState.SUCCEEDED` 判断就会静默失效，
        任务会一直轮询到超时。这里当场转换，非法值当场报错。
        """
        object.__setattr__(self, "state", GenerationState(self.state))
        object.__setattr__(self, "progress", max(0, min(100, int(self.progress))))


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """一个可被业务选择的模型。

    `param_schema` 是给前端渲染表单用的最小描述，例如：
        {"durationSec": {"type": "int", "min": 5, "max": 12, "default": 5},
         "aspectRatio": {"type": "enum", "options": ["9:16", "16:9"], "default": "9:16"}}
    """

    id: str
    label: str
    capability: Capability
    provider: str
    param_schema: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""

    def defaults(self) -> dict[str, Any]:
        return {
            key: schema.get("default")
            for key, schema in self.param_schema.items()
            if schema.get("default") is not None
        }


@runtime_checkable
class ModelProvider(Protocol):
    """供应商适配器。实现三个方法即可接入一个新厂商。"""

    name: str
    capabilities: frozenset[Capability]
    models: tuple[ModelSpec, ...]

    async def submit(self, request: GenerationRequest) -> str:
        """提交生成任务，返回供应商侧任务 ID。"""
        ...

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        """查询任务状态。"""
        ...

    async def cancel(self, external_id: str) -> None:
        """取消任务；不支持时实现为空即可。"""
        ...
