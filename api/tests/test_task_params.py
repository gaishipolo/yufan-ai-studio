"""任务参数解析：模型 schema 是唯一判据。"""

from __future__ import annotations

import pytest


def test_style_preset_follows_the_model_schema() -> None:
    """声明了 `stylePreset` 的模型：按 options 校验；没声明：拒绝；空值：不写。"""
    from core.exceptions import BadRequest
    from infra.ai.base import Capability, ModelSpec
    from modules.task.params import resolve_style_preset

    def spec(param_schema: dict) -> ModelSpec:
        return ModelSpec(
            id="m",
            label="演示",
            provider="probe",
            capability=Capability.VIDEO,
            param_schema=param_schema,
        )

    declaring = spec({"stylePreset": {"type": "enum", "options": ["film", "anime"]}})
    assert resolve_style_preset(declaring, "film") == "film"
    assert resolve_style_preset(declaring, "  ") is None
    assert resolve_style_preset(declaring, None) is None
    with pytest.raises(BadRequest, match="风格"):
        resolve_style_preset(declaring, "cyberpunk")

    silent = spec({})
    with pytest.raises(BadRequest, match="没有声明"):
        resolve_style_preset(silent, "film")
