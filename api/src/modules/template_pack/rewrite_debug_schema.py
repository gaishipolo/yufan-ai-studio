"""LLM 调试台：选模板、改 system prompt / 参数、看改写与最终提示词。

这不是模板库的填表接口，也不创建任务：只做一次模型改写 + 纯函数编译预览。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from core.schema import CamelModel
from core.template_spec import MAX_PROMPT
from modules.template_pack.randomness import DEFAULT_MAX_TOKENS
from modules.template_pack.spec_schema import StrictIn


class DebugTemplateOut(CamelModel):
    key: str
    name: str
    version: int
    status: str


class RandomRewriteSettingsOut(CamelModel):
    model: str
    system_prompt: str
    temperature: float
    max_tokens: int | None = None
    extra_params: dict[str, Any] = Field(default_factory=dict)


class RewriteDebugOptionsOut(CamelModel):
    templates: list[DebugTemplateOut] = Field(default_factory=list)
    default_system_prompt: str
    default_temperature: float
    max_source_chars: int
    #: 当前生产实际生效的随机改写配置（没保存过就是默认值）。
    current_settings: RandomRewriteSettingsOut
    #: 是否有人保存过覆盖配置。
    customized: bool = False


class RandomRewriteSettingsIn(StrictIn):
    model: str = Field(default="llm/default", min_length=1, max_length=80)
    system_prompt: str = Field(min_length=1, max_length=MAX_PROMPT)
    temperature: float = Field(default=1.0, ge=0, le=2)
    max_tokens: int | None = Field(default=DEFAULT_MAX_TOKENS, ge=1, le=32000)
    extra_params: dict[str, Any] = Field(default_factory=dict)


class RewriteDebugIn(StrictIn):
    template_key: str = Field(min_length=1, max_length=40)
    #: 不传 = 当前发布版本。
    version: int | None = Field(default=None, ge=1)
    #: `llm/default` 走平台降级链；也可以传具体平台模型 id。
    model: str = Field(default="llm/default", min_length=1, max_length=80)
    system_prompt: str = Field(min_length=1, max_length=MAX_PROMPT)
    temperature: float = Field(default=1.0, ge=0, le=2)
    #: 默认 8000；可覆盖。max_tokens 只是模型输出上限，不强制生成长文本。
    max_tokens: int | None = Field(default=DEFAULT_MAX_TOKENS, ge=1, le=32000)
    #: 透传给 LLM 的额外参数（top_p / frequency_penalty ...）。
    extra_params: dict[str, Any] = Field(default_factory=dict)


class RewriteDebugUsageOut(CamelModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class RewriteDebugOut(CamelModel):
    ok: bool
    error: str = ""
    requested_model: str = ""
    system_prompt: str
    user_payload: str
    request_params: dict[str, Any] = Field(default_factory=dict)
    raw_output: str = ""
    rewritten_texts: dict[str, str] = Field(default_factory=dict)
    final_prompt: str = ""
    reference_library_ids: list[str] = Field(default_factory=list)
    text_library_ids: list[str] = Field(default_factory=list)
    compiled_params: dict[str, Any] = Field(default_factory=dict)
    prompt_length: int = 0
    prompt_limit: int = 0
    capability: str = ""
    llm_model: str = ""
    llm_platform_model: str = ""
    finish_reason: str = ""
    usage: RewriteDebugUsageOut = Field(default_factory=RewriteDebugUsageOut)
    latency_ms: int = 0
    #: 为了编译预览而合成素材的槽位；只用于展示，不会创建任务。
    synthetic_slots: list[str] = Field(default_factory=list)


__all__ = [
    "DebugTemplateOut",
    "RandomRewriteSettingsIn",
    "RandomRewriteSettingsOut",
    "RewriteDebugIn",
    "RewriteDebugOptionsOut",
    "RewriteDebugOut",
    "RewriteDebugUsageOut",
]