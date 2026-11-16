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
    # 只能由**自己的业务链路**调用的模型：内部评测、需要专属档案的渠道等。
    # 它们在目录接口里不出现，但 `resolve()` 仍能解析（业务链按 id 直接调）。
    #
    # 为什么必须有这个标记：某条链的模型带着「调用档案标识」之类的前置校验时，
    # 出现在通用生图 / 视频页的下拉里必然 400，而报错指向「标识不匹配」——
    # 没人能从这句话推出「这个模型不该出现在这里」。
    # 三个 `*-replacement` 曾经属于这一类；2026-11-13 起通用入口会补出归档
    # 工作区，它们已公开，不再标 `internal`。
    internal: bool = False
    # 占位模型：**实现是假的**，但 id/label 长得像真的第三方模型。
    #
    # 为什么要有这个标记（而不是复用 `internal`）：`internal` 的语义是
    # 「只能由自己的业务链路调用」（专属档案校验）；而占位模型的问题是
    # 另一回事——**它会出现在用户的模型选择器里，
    # 选中后跑完拿到一张 `example.com/mock/...` 的占位图**，全程没有任何报错。
    # 用户看到「Seedream 3」以为接了字节的豆包，其实什么都没接。
    #
    # 典型来源是 `drivers/echo.py`（本地开发占位驱动）。示例驱动靠文件名
    # `_` 前缀不自动载入；echo 不带前缀是故意的——离线联调按 id 提交要能
    # 解析得到。挡在选择器外靠 `placeholder`，不靠文件名。
    #
    # 与 `internal` 一样，`resolve()` 仍能解析到它：模板库迁移到 `drivers/`
    # 之前，老链路按 id 提交照常能跑（只是拿占位结果）。区别只在于
    # **不给人挑**（`public_models()` 过滤掉）。
    placeholder: bool = False
    # 这个模型最多收几张参考图（图生图 / 多图合成）。`0` = 纯文生图。
    #
    # 为什么放在 ModelSpec 而不是 `param_schema`：参数 schema 的值域只有
    # string / number / bool，装不下一批图片；参考图也不是「模型参数」，它是
    # 调用输入，走 `GenerationRequest.reference_urls` 这条独立通道（视频侧同一个
    # 字段）。但「要不要显示参考图控件」必须由**模型**说了算——给纯文生图模型
    # 也画一个上传框，用户传了不会被用上：驱动压根不读，图照样出，只是完全
    # 不像参考图，而界面上没有任何异常。
    max_references: int = 0
    # 这个模型能收哪几类参考素材。**空元组 = 纯文生**（没有任何参考入口）。
    #
    # 为什么要有这个声明：`max_references` 只说「收几张」，没说「收什么类型」。
    # 前端此前把选择器写死成「只选图片、最多 4 张」，于是视频替换模型
    # （要「原片视频 + 参考图 + 音频」）在界面上根本选不到视频与音频 ——
    # 后端那条链明明支持，类型筛选却把用户挡在外面。
    #
    # 与 `max_references` 同源：都由**模型**说了算。前端据此渲染类型筛选
    # 与数量上限，不在界面里写死。
    reference_kinds: tuple[str, ...] = ()
    # 这个模型能不能跑**替换链**（按「原片 + 素材包」替换人物/产品/场景）。
    #
    # 为什么必须由模型声明、不能从槽位组合推断：替换链要按「原片 + 素材包」找回
    # 对应模板，如果在 spec 上按"有 fixed video 槽 + 有 pack 槽"猜，普通的多图
    # 模板会被误判成可替换模板，然后在替换链路里以「没有绑定档案」失败 ——
    # 那是个和用户操作完全无关的错。能不能替换是**模型/渠道**的事实。
    #
    # 判据只有这一处：`GET /models` 出参带 `replacement`，模板目录按它筛。
    replacement: bool = False

    def defaults(self) -> dict[str, Any]:
        return {
            key: schema.get("default")
            for key, schema in self.param_schema.items()
            if schema.get("default") is not None
        }


# --------------------------------------------------------------------------- #
# 可选能力：**用协议 + isinstance 表达，不要用 getattr 探测**
#
# 项目里「驱动可以多实现一点东西」一直靠 `getattr(provider, "xxx", None)` 探测。
# 问题是它不是契约：字段名拼错、被改名、或者哪天挪到别处，探测只会安静地返回
# None，然后**退化成默认行为** —— 轮询节奏退回 2 秒、提交预算退回默认、同步供应商
# 被当成异步去 resume。协议把这件事变成可检查的：实现方照着写，调用方
# `isinstance`，改名会立刻在这里失配而不是悄悄降级。
#
# 三个能力刻意分开：`supports_poll` 与 `poll_interval_sec` 经常只有一个。
# 合成一个协议的话，声明了 `supports_poll=True` 但没写间隔的供应商会被判成
# 「不支持轮询」—— 那正是最危险的方向。
# --------------------------------------------------------------------------- #


@runtime_checkable
class PollCapable(Protocol):
    """能查上游任务状态。同步供应商不声明（缺省按「不能续查」处理）。"""

    supports_poll: bool


@runtime_checkable
class PollPacing(Protocol):
    """自带查询节奏（秒）。没声明的按 `GENERATION_POLL_INTERVAL_SEC`。"""

    poll_interval_sec: float


@runtime_checkable
class SubmitBudget(Protocol):
    """提交这一步的预算（秒）。没声明按框架默认值。"""

    submit_timeout_sec: float


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
    # 「谁先被 import」决定——`drivers/` 的扫描顺序是装配细节，增删一个
    # 驱动文件就会静默换掉默认模型。
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


