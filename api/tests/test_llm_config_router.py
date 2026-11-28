"""LLM 配置页纯逻辑守卫：测试端点选哪个模型。"""

from __future__ import annotations

import pytest

from core.exceptions import BadRequest
from modules.llm_config.router import _fetch_target, _select_model
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


def test_新供应商可以直接用草稿网关和密钥拉模型() -> None:
    """新增弹窗还没保存：网关与密钥由请求体带过来，不必先存一遍。"""
    url, key = _fetch_target(None, "", "https://8.8.8.8/v1/", "sk-draft")
    assert (url, key) == ("https://8.8.8.8/v1", "sk-draft")


def test_编辑老供应商留空则继续用已保存的网关和密钥() -> None:
    saved = _provider((ModelMapping("x/a", "A"),))
    assert _fetch_target(saved, "sk-saved", "", "") == (
        "https://x.example/v1",
        "sk-saved",
    )


def test_编辑时改网关但没重填密钥仍用已保存密钥() -> None:
    saved = _provider((ModelMapping("x/a", "A"),))
    assert _fetch_target(saved, "sk-saved", "https://8.8.8.8/v2", "") == (
        "https://8.8.8.8/v2",
        "sk-saved",
    )


def test_新供应商缺网关或密钥是400() -> None:
    with pytest.raises(BadRequest, match="网关地址和密钥"):
        _fetch_target(None, "", "https://8.8.8.8/v1", "")
    with pytest.raises(BadRequest, match="网关地址和密钥"):
        _fetch_target(None, "", "", "sk-draft")


def test_拉取模型也过SSRF校验() -> None:
    """「只读」不放宽：草稿网关与保存走同一套公网校验。"""
    with pytest.raises(BadRequest, match="非公网"):
        _fetch_target(None, "", "http://127.0.0.1:11434/v1", "sk-draft")