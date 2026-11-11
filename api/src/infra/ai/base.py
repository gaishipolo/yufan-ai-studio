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


# 异步供应商返回任务 ID（需要轮询）；同步供应商直接返回终态快照（不需要轮询）。
SubmitResult = str | GenerationProgress


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
    # 只能由**自己的业务链路**调用的模型：替换工作台的渠道、内部评测等。
    # 它们在目录接口里不出现，但 `resolve()` 仍能解析（替换链正是按 id 直接调的）。
    #
    # 为什么必须有这个标记：替换链的模型（`bailian-wan3-*` / `libtv-wan3-*`）带
    # 一道「调用档案标识」校验，`params` 里没有 `archiveRunId` 就 400。它们出现在
    # 通用生图 / 视频页的下拉里时，用户选中必然失败，而报错是「标识不匹配」——
    # 没人能从这句话推出「这个模型不该出现在这里」。
    internal: bool = False

    def defaults(self) -> dict[str, Any]:
        return {
            key: schema.get("default")
            for key, schema in self.param_schema.items()
            if schema.get("default") is not None
        }


@runtime_checkable
class ModelProvider(Protocol):
    """供应商适配器。实现三个方法即可接入一个新厂商。

    供应商有**两种形态**，协议两种都收：

    - **异步**（主流视频厂商）：`submit` 返回任务 ID 字符串，框架按节奏轮询；
    - **同步**（多数图片厂商）：`submit` 自己等结果，直接返回终态
      `GenerationProgress`，框架拿到后不再轮询。

    同步形态下 `submit` 会占住一个 worker 槽位直到出图（Agnes 实测约 13 秒），
    这是供应商的事实约束，不是框架的疏漏；`poll` 在这条路径上不会被调用。
    """

    name: str
    capabilities: frozenset[Capability]
    models: tuple[ModelSpec, ...]

    # 同步供应商设 `supports_poll = False`，异步供应商显式设 `True`。
    # **缺省按 False 处理**（见 generation/recovery.py）：下一个同步供应商忘了
    # 写这一行时，宁可把它当「不能续查」置失败等人核对，也不能 resume() 到
    # 一个恒返回 FAILED 的 poll() 上把记录判死（R11-01 / R12-05）。
    # 不写进 Protocol 成员，是为了不强迫所有历史实现立刻补字段。
    # supports_poll: bool = True

    # 同样可选的声明式字段：`preferred_for = frozenset({Capability.IMAGE})`
    # 表示「我这个供应商的模型是该能力的默认首选」。不写则沿用「注册顺序第一个」
    # （见 `ModelRouter._apply_preferred`）。为什么需要它：默认模型不该由
    # 「谁先被 import」决定——供应商来源有 `AI_PROVIDERS` 与 `drivers/` 两条，
    # 顺序是装配细节，换个接入方式就会静默换掉默认模型。
    # preferred_for: frozenset[Capability]

    async def submit(self, request: GenerationRequest) -> SubmitResult:
        """提交生成任务。

        返回 `str`（供应商侧任务 ID，需轮询）或 `GenerationProgress`
        （同步供应商的终态快照，框架直接落结果）。
        """
        ...

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        """查询任务状态。同步供应商可不实现（返回 FAILED 说明不支持即可）。"""
        ...

    async def cancel(self, external_id: str) -> None:
        """取消任务；不支持时实现为空即可。"""
        ...


@runtime_checkable
class InputPreparer(Protocol):
    """可选能力：提交前先把素材传到供应商侧（可选协议，不实现也能跑）。

    很多视频模型只接受**公网 URL** 作参考素材，不认本地文件；这类供应商在
    提交前要把素材直传自己的对象存储，再把直链拼进请求体。这一步既不是
    「提交」也不是「查询」，塞进 `ModelProvider` 会让所有供应商都得实现一个
    空方法，所以单列成可选协议：`isinstance(provider, InputPreparer)` 就是
    「这个渠道需要先准备素材」。

    调用方（替换链）通过 `ModelRouter` 拿到实例再判类型，**不 import 具体驱动**
    ——业务模块之间不允许横向依赖，而驱动归 `modules/model_driver` 管。
    """

    def unavailable_reason(self) -> str:
        """空串表示可用；否则是给用户看的不可用原因（如未配置密钥）。"""
        ...

    async def prepare_inputs(self, run_id: str, model: str = "") -> dict[str, Any]:
        """上传 run 工作区里的素材，返回可缓存、提交时直接复用的请求体。"""
        ...
