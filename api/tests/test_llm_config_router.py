"""LLM 配置页纯逻辑守卫：测试端点选哪个模型。"""

from __future__ import annotations

import pytest

from core.exceptions import BadRequest
from modules.llm_config.router import _select_model
from modules.llm_config.store import ModelMapping, ProviderOut


def _provider(
    models: tuple[ModelMapping, ...],
    *,
    config_error: str = "",
) -> ProviderOut:
    return ProviderOut(
        name="x",
        base_url="https://x.example/v1",
        api_key_configured=True,
        api_key_masked="",
        api_key_fingerprint="",
        models=models,
        source="env_file",
        config_error=config_error,
    )


def test_留空取第一个显式传则精确匹配() -> None:
    provider = _provider((ModelMapping("x/a", "A"), ModelMapping("x/b", "B")))
    assert _select_model(provider, "").id == "x/a"
    assert _select_model(provider, "x/b").id == "x/b"


def test_显式未知模型是400而不是静默回落() -> None:
    provider = _provider((ModelMapping("x/a", "A"),))
    with pytest.raises(BadRequest, match="不在该供应商的映射里"):
        _select_model(provider, "x/ghost")


def test_没有模型与模型表配置错误分别说清楚() -> None:
    with pytest.raises(BadRequest, match="还没有配置模型映射"):
        _select_model(_provider(()), "")
    with pytest.raises(BadRequest, match="模型映射配置有误"):
        _select_model(
            _provider((), config_error="LLM_X_MODELS：不是合法 JSON"),
            "",
        )