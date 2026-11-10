"""统一生成接口（注入口）：下游模块调用模型驱动的唯一入口。

驱动文件长什么样、`ModelRouter` 怎么解析模型、生成记录存在哪张表，都是
`modules/model_driver` 的内部实现。其他模块只认识这里的一组数据类型与协议：

    from core.ports.model_generation import SubmitSpec, get_model_generation

    ticket = await get_model_generation().submit(
        SubmitSpec(model="seedream-3", prompt="...", owner_id=actor.id)
    )
    ticket = await get_model_generation().query(ticket.task_id)

数据类型刻意只用标准库与 `str`：core 层不需要认识 `infra.ai` 的枚举，
端口协议才不会因为上层换供应商而改签名。

未注册时 fail-closed（抛 503），不返回「空模型列表」——那会把装配故障
报成「用户没配模型」，是最难排查的一类问题。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from core.exceptions import Unavailable
from core.ports.host import Port

__all__ = [
    "DriverSummary",
    "GenerationTicket",
    "ModelGeneration",
    "ModelSummary",
    "SubmitSpec",
    "get_model_generation",
    "has_model_generation",
    "register_model_generation",
    "reset_model_generation",
]


@dataclass(frozen=True, slots=True)
class SubmitSpec:
    """一次生成请求。`source` 由调用方填，便于在任务列表里追溯是谁发起的。"""

    model: str
    prompt: str
    owner_id: str
    params: Mapping[str, Any] = field(default_factory=dict)
    reference_urls: tuple[str, ...] = ()
    name: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class GenerationTicket:
    """一次生成任务的只读快照。`status` 见 `generation.spec` 的状态常量。"""

    task_id: str
    model: str
    capability: str
    status: str
    progress: int = 0
    external_id: str | None = None
    output_url: str | None = None
    cover_url: str | None = None
    error: str | None = None
    owner_id: str = ""
    source: str = ""
    submit_attempts: int = 0
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ModelSummary:
    """一个可被选择的模型（给下游做能力判断 / 表单渲染）。"""

    id: str
    label: str
    capability: str
    provider: str
    param_schema: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass(frozen=True, slots=True)
class DriverSummary:
    """一个驱动文件的加载结果。`loaded=False` 时 `error` 说明为什么没接上。"""

    name: str
    path: str
    label: str
    version: str
    capabilities: tuple[str, ...]
    model_count: int
    supports_poll: bool
    loaded: bool
    error: str = ""


@runtime_checkable
class ModelGeneration(Protocol):
    """对下游暴露的标准生成接口。

    `submit` 是**编排入口**而不是「同步等结果」：它落库并入队后立即返回，
    后续提交、轮询、重试、超时、恢复由 `generation.runner` 统一接管。
    """

    async def submit(self, request: SubmitSpec) -> GenerationTicket: ...
    async def query(self, task_id: str) -> GenerationTicket | None: ...
    async def cancel(self, task_id: str) -> GenerationTicket | None: ...
    def models(self, capability: str = "") -> tuple[ModelSummary, ...]: ...
    def drivers(self) -> tuple[DriverSummary, ...]: ...


_generation: Port[ModelGeneration] = Port("model-generation", multi=False)


def register_model_generation(impl: ModelGeneration) -> ModelGeneration:
    return _generation.provide(impl, owner="modules.model_driver")


def reset_model_generation() -> None:
    """仅测试使用。"""
    _generation.reset()


def has_model_generation() -> bool:
    return _generation.has()


def get_model_generation() -> ModelGeneration:
    impl = _generation.single()
    if impl is None:
        raise Unavailable("模型驱动模块尚未装配，统一生成接口不可用")
    return impl
