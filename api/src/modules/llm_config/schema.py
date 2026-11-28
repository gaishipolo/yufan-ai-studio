"""LLM 配置页出入参。snake_case 书写，序列化自动 camelCase。"""

from __future__ import annotations

from pydantic import Field

from core.schema import CamelModel

__all__ = [
    "FetchModelsOut",
    "RenameProviderIn",
    "LlmModelOut",
    "LlmProviderOut",
    "LlmProvidersOut",
    "ModelChainIn",
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
    source: str = "none"
    #: 该供应商自己的配置错误（模型列表解析失败）；其它供应商照常返回。
    config_error: str = ""


class LlmProvidersOut(CamelModel):
    providers: list[LlmProviderOut] = Field(default_factory=list)
    # 全局默认模型/降级链；第一项即默认模型。
    model_chain: list[str] = Field(default_factory=list)
    #: LLM_MODEL_CHAIN 解析失败时的原因；此时 model_chain 为空，页面仍可展示供应商。
    model_chain_error: str = ""


class RenameProviderIn(CamelModel):
    #: 新供应商名。改名会同步迁移 env 键、模型 id 前缀和降级链。
    name: str


class SaveProviderIn(CamelModel):
    name: str
    base_url: str
    # 空串 = 不改已有密钥（与 runtime_config 同口径）
    api_key: str = ""
    models: list[LlmModelOut] = Field(default_factory=list)


class FetchModelsIn(CamelModel):
    """拉取模型时的草稿覆盖：新供应商还没保存，只能把网关/密钥带过来。

    留空 = 用已保存的配置（编辑老供应商时不必重填密钥）。
    """

    base_url: str = ""
    api_key: str = ""


class ModelChainIn(CamelModel):
    models: list[str] = Field(default_factory=list)


class UpstreamModelOut(CamelModel):
    id: str


class FetchModelsOut(CamelModel):
    models: list[UpstreamModelOut] = Field(default_factory=list)
