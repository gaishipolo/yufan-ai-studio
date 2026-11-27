"""LLM 配置页出入参。snake_case 书写，序列化自动 camelCase。"""

from __future__ import annotations

from pydantic import Field

from core.schema import CamelModel

__all__ = [
    "FetchModelsOut",
    "LlmModelOut",
    "LlmProviderOut",
    "LlmProvidersOut",
    "SaveProviderIn",
    "UpstreamModelOut",
]


class LlmModelOut(CamelModel):
    id: str
    upstream: str


class LlmProviderOut(CamelModel):
    name: str
    base_url: str
    api_key_configured: bool = False
    api_key_masked: str = ""
    api_key_fingerprint: str = ""
    models: list[LlmModelOut] = Field(default_factory=list)
    source: str = "default"


class LlmProvidersOut(CamelModel):
    providers: list[LlmProviderOut] = Field(default_factory=list)


class SaveProviderIn(CamelModel):
    name: str
    base_url: str
    # 空串 = 不改已有密钥（与 runtime_config 同口径）
    api_key: str = ""
    models: list[LlmModelOut] = Field(default_factory=list)


class UpstreamModelOut(CamelModel):
    id: str


class FetchModelsOut(CamelModel):
    models: list[UpstreamModelOut] = Field(default_factory=list)
