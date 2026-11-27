"""LLM 能力层：单次调用的协议 + OpenAI 兼容客户端。

与 `infra/ai`（生图视频的 submit/poll 范式）并列；这里是同步一问一答，
产物只有文本，不落库、不进队列、不建业务模块。

业务模块可直接 `from infra.llm import complete, LlmMessage, LlmRequest`：
infra → 业务 单向依赖成立，不需要注入口（见设计文档 §3.2）。
"""

from __future__ import annotations

from infra.llm.base import (
    LlmMessage,
    LlmRequest,
    LlmResponse,
    LlmToolCall,
    LlmUsage,
)
from infra.llm.client import OpenAICompatibleClient, complete

__all__ = [
    "LlmMessage",
    "LlmRequest",
    "LlmResponse",
    "LlmToolCall",
    "LlmUsage",
    "OpenAICompatibleClient",
    "complete",
]
