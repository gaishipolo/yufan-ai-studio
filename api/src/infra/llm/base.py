"""LLM 调用的能力层协议（L1）。

与 `infra/ai/base.py` 的生成协议并列、互不混用：
- `infra/ai` 是 submit/poll/cancel 的**生成范式**（产物是文件/URL）；
- 这里是单次同步调用（产物是文本）。

协议从第一天就用**消息数组**：将来 agent 每轮往数组尾部追加即可，
不必改客户端。V1 不实现 agent，但预留的字段都得现在定好，
否则 agent 一来就要改 L1 —— 那是「现在省事、以后推倒重来」的地方。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class LlmMessage:
    """一条消息。字段名与 OpenAI 兼容协议对齐，不另造一套。"""

    role: str  # system | user | assistant | tool
    content: str
    name: str = ""  # 作者名（tool 角色时是工具名）
    # role=tool 时必填：agent 把工具结果喂回去，上游靠它和 assistant 的
    # tool_calls[].id 配对。V1 不拼 tool 消息，但字段必须现在就有。
    tool_call_id: str = ""


@dataclass(frozen=True, slots=True)
class LlmRequest:
    """单次调用请求。

    `model` 是平台内 id（如 `llm/qwen-plus`），不是上游 model 名 ——
    翻译在供应商层做，调用方永远只认平台 id，换供应商不改业务代码。
    """

    model: str
    messages: tuple[LlmMessage, ...]
    # 必须 default_factory：实测 `params: Mapping = ...` 时
    # `LlmRequest(...).params is Ellipsis`，不传就把省略号透给上游。
    params: Mapping[str, Any] = field(default_factory=dict)
    request_id: str = ""


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """token 用量。随响应返回，将来由 agent 模块落业务表记账。"""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class LlmToolCall:
    """一次工具调用。V1 解析但**不执行**。"""

    id: str
    name: str
    # JSON 字符串（OpenAI 原样），不是 dict —— agent 将来只 json.loads 一次。
    arguments: str


@dataclass(frozen=True, slots=True)
class LlmResponse:
    """单次调用的返回。产物只有文本，不落库、不进队列。"""

    text: str
    model: str
    finish_reason: str  # stop | length | tool_calls | ...
    usage: LlmUsage
    tool_calls: tuple[LlmToolCall, ...] = ()
    raw_id: str = ""


__all__ = [
    "LlmMessage",
    "LlmRequest",
    "LlmResponse",
    "LlmToolCall",
    "LlmUsage",
]
