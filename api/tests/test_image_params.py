"""受理前参数校验：把「提交成功、几秒后失败」变成「立刻 400」。

这两条都来自实测：前端 `ImagePage` 的尺寸下拉用的是全角 `×`，数量下拉给了
1/2/4 而 Agnes 只接受 n=1。改前两个都会**受理成功**（201），然后后台异步失败，
用户在列表里只看到一张占位图——错误信息躺在 `errorMessage` 字段里，页面上没有任何地方渲染它。
"""

from __future__ import annotations

import pytest

from infra.ai.base import Capability, ModelSpec
from modules.image.service import _validate_params


def _spec(**overrides: object) -> ModelSpec:
    schema = {
        "size": {
            "type": "enum",
            "options": ["1024x1024", "1024x1536", "1536x1024"],
            "default": "1024x1024",
        },
        "count": {"type": "int", "min": 1, "max": 1, "default": 1},
    }
    return ModelSpec(
        id="agnes-image-2.5-flash",
        label="Agnes Image 2.5 Flash",
        capability=Capability.IMAGE,
        provider="agnes",
        param_schema=schema,  # type: ignore[arg-type]
        **overrides,  # type: ignore[arg-type]
    )


class _Payload:
    def __init__(
        self,
        size: str = "1024x1024",
        count: int = 1,
        params: dict[str, object] | None = None,
    ) -> None:
        self.size = size
        self.count = count
        self.params = params or {}


@pytest.mark.parametrize("size", ["1024×1536", "1024×1024", "512x512", "1024X1536"])
def test_rejects_size_outside_declared_options(size: str) -> None:
    from core.exceptions import BadRequest

    with pytest.raises(BadRequest) as exc:
        _validate_params(_spec(), _Payload(size=size))  # type: ignore[arg-type]

    message = str(exc.value)
    assert "可选" in message, "报错要说清可选值，否则前端只能猜"
    assert "x" in message, "顺手提示半角 x：全角乘号是这次真实踩到的坑"


def test_accepts_declared_sizes() -> None:
    for size in ("1024x1024", "1024x1536", "1536x1024"):
        _validate_params(_spec(), _Payload(size=size))  # type: ignore[arg-type]


@pytest.mark.parametrize("count", [2, 4])
def test_rejects_count_above_model_capability(count: int) -> None:
    """供应商只支持 n=1。声明里写 4 等于承诺一个做不到的能力。"""
    from core.exceptions import BadRequest

    with pytest.raises(BadRequest) as exc:
        _validate_params(_spec(), _Payload(count=count))  # type: ignore[arg-type]
    assert "范围" in str(exc.value)


def test_agnes_declares_count_max_one() -> None:
    """声明必须和供应商真实能力一致——`GET /models` 就是前端渲染表单的依据。"""
    from modules.model_driver.drivers.agnes import MODELS

    for model in MODELS:
        count = (model.param_schema.get("count") or {}) if model.param_schema else {}
        assert count.get("max") == 1, f"{model.id} 声明了供应商不支持的张数"


def test_missing_schema_does_not_block() -> None:
    """模型没声明某个参数时不校验：不要让「没写」变成「不能用」。"""
    spec = ModelSpec(
        id="echo-image",
        label="Echo",
        capability=Capability.IMAGE,
        provider="echo",
        param_schema={},
    )
    _validate_params(spec, _Payload(size="任意", count=9))  # type: ignore[arg-type]


# ---------------- 通用参数：规则只来自 param_schema ----------------


def _schema_spec(schema: dict[str, object]) -> ModelSpec:
    """只有一份自定义 schema 的模型；用于验证「不认识具体字段名」这件事。"""
    return ModelSpec(
        id="custom-image",
        label="Custom",
        capability=Capability.IMAGE,
        provider="agnes",
        param_schema=schema,  # type: ignore[arg-type]
    )


def test_resolves_declared_params_to_provider_types() -> None:
    """前端表单里的一切都是字符串，落到供应商前必须收敛成声明的类型。"""
    spec = _schema_spec(
        {
            "quality": {"type": "enum", "options": ["low", "high"], "default": "high"},
            "steps": {"type": "int", "min": 10, "max": 30, "step": 10},
            "cfg": {"type": "float", "min": 1.0, "max": 10.0},
            "hires": {"type": "bool"},
            "negativePrompt": {"type": "string", "maxLength": 8},
        }
    )
    payload = _Payload(
        size="",
        count=0,
        params={
            "quality": "low",
            "steps": "20",
            "cfg": "7.5",
            "hires": "true",
            "negativePrompt": "模糊",
        },
    )
    assert _validate_params(spec, payload) == {  # type: ignore[arg-type]
        "quality": "low",
        "steps": 20,
        "cfg": 7.5,
        "hires": True,
        "negativePrompt": "模糊",
    }


def test_numeric_enum_option_keeps_its_type() -> None:
    """`options: [1, 2]` 回填的必须是数字：供应商按数字校验，字符串会被拒。"""
    spec = _schema_spec({"seed": {"type": "enum", "options": [1, 2], "default": 1}})
    resolved = _validate_params(spec, _Payload(params={"seed": "2"}))  # type: ignore[arg-type]
    assert resolved["seed"] == 2
    assert isinstance(resolved["seed"], int)


def test_defaults_fill_in_when_caller_omits_a_param() -> None:
    spec = _schema_spec({"quality": {"type": "enum", "options": ["low", "high"], "default": "low"}})
    assert _validate_params(spec, _Payload())["quality"] == "low"  # type: ignore[arg-type]


def test_param_without_value_or_default_is_not_sent() -> None:
    """没有默认值也没传：不下发，让供应商用自己的默认，别编一个。"""
    spec = _schema_spec({"seed": {"type": "int", "min": 0, "max": 2**31 - 1}})
    assert "seed" not in _validate_params(spec, _Payload())  # type: ignore[arg-type]


def test_unknown_param_is_rejected() -> None:
    """打错字（`szie`）被静默忽略的话，用户改的值从来没生效过。"""
    from core.exceptions import BadRequest

    with pytest.raises(BadRequest) as exc:
        _validate_params(_spec(), _Payload(params={"szie": "512x512"}))  # type: ignore[arg-type]
    assert "szie" in str(exc.value)
    assert exc.value.details.get("availableParams") == ["size", "count"]


def test_top_level_size_wins_over_params() -> None:
    """两处都写时语义要确定，否则首提与重试可能用不同尺寸。"""
    spec = _schema_spec({"size": {"type": "enum", "options": ["1x1", "2x2"], "default": "1x1"}})
    payload = _Payload(size="2x2", params={"size": "1x1"})
    assert _validate_params(spec, payload)["size"] == "2x2"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ({"type": "int", "min": 1, "max": 5}, 9),
        ({"type": "int", "min": 1, "max": 5}, "abc"),
        ({"type": "int"}, True),
        ({"type": "bool"}, "maybe"),
        ({"type": "string", "maxLength": 3}, "太长了吧"),
        ({"type": "enum", "options": ["a"]}, "b"),
    ],
)
def test_illegal_values_are_rejected_with_field_name(
    field: dict[str, object], value: object
) -> None:
    """错误里必须点名是哪个字段：表单有十几个控件时，「参数有误」等于没说。"""
    from core.exceptions import BadRequest

    with pytest.raises(BadRequest) as exc:
        _validate_params(_schema_spec({"opt": field}), _Payload(params={"opt": value}))  # type: ignore[arg-type]
    assert exc.value.details.get("field") == "opt"


def test_step_must_match_declared_ladder() -> None:
    """声明了 step 就不是「范围内就行」：档位之外的值供应商不认。"""
    from core.exceptions import BadRequest

    spec = _schema_spec({"steps": {"type": "int", "min": 10, "max": 30, "step": 10}})
    with pytest.raises(BadRequest) as exc:
        _validate_params(spec, _Payload(params={"steps": 15}))  # type: ignore[arg-type]
    assert exc.value.details.get("options") == [10, 20, 30]
    assert _validate_params(spec, _Payload(params={"steps": 30}))["steps"] == 30  # type: ignore[arg-type]
