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
    def __init__(self, size: str = "1024x1024", count: int = 1) -> None:
        self.size = size
        self.count = count


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
    from infra.ai.agnes import MODELS

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
