"""「模型目录 → 创建入参」的校验：入口就把非法请求挡住。

为什么必须在同步接口里校验，而不是等 worker 里报错：

- 供应商侧错误发生在异步流程里，用户早已收到 201「任务已创建」，
  只能在任务中心看到一条失败记录，且错误信息是 `BadRequest` 的通用文案；
- 提交失败会走重试与退避，把「参数写错了」拖成几秒到十几秒的「卡住」；
- 真实供应商场景下，一次非法提交也可能已经产生调用与费用。

校验规则全部来自模型自己的 `param_schema`（供应商声明），**不在这里写死**
时长上限、比例选项——新增模型不需要改这个文件。
"""

from __future__ import annotations

from typing import Any

from core.exceptions import BadRequest
from infra.ai.base import Capability, ModelSpec
from infra.ai.router import ModelRouter


def resolve_model(
    router: ModelRouter, model: str | None, *, capability: Capability = Capability.VIDEO
) -> ModelSpec:
    """按 id 或 label 解析模型；不传则取该能力的默认模型。

    解析失败时把可用模型列出来——前端拿到的是「该怎么改」，而不是「参数有误」。
    """
    if not model:
        default_id = router.default_model_or_none(capability)
        if default_id is None:
            raise BadRequest(f"尚未注册任何 {capability.value} 模型")
        spec = router.spec_for(default_id)
        if spec is None:  # pragma: no cover - default_model 一定来自注册表
            raise BadRequest(f"默认模型 {default_id!r} 不在目录中")
        return spec

    spec = router.spec_for(model)
    if spec is None:
        # 只提示公开模型：`internal=True` / `placeholder=True` 的按 id 仍能解析，
        # 但列进「可用模型」会把人往 400 / 占位图上引
        public = router.public_models(capability)
        available = [f"{s.id}（{s.label}）" for s in public]
        raise BadRequest(
            f"未知模型 {model!r}，可用模型: {', '.join(available) or '无'}",
            details={"availableModels": [s.id for s in public]},
        )
    if spec.capability is not capability:
        raise BadRequest(
            f"模型 {model!r} 的能力是 {spec.capability.value}，不能用于 {capability.value} 任务"
        )
    return spec


def resolve_duration(spec: ModelSpec, duration_sec: int | None) -> int:
    """按时长 schema 校验并给出最终值（不传则用 schema 默认值）。"""
    schema = _param(spec, "durationSec")
    default = _as_int(schema.get("default"), fallback=5)
    value = default if duration_sec is None else int(duration_sec)

    minimum = _as_int(schema.get("min"), fallback=1)
    maximum = _as_int(schema.get("max"), fallback=600)
    step = _as_int(schema.get("step"), fallback=1)

    if not minimum <= value <= maximum:
        raise BadRequest(
            f"时长 {value}s 超出模型 {spec.label} 的支持范围（{minimum}-{maximum}s）",
            details={"field": "durationSec", "min": minimum, "max": maximum},
        )
    if step > 1 and (value - minimum) % step:
        options = list(range(minimum, maximum + 1, step))
        raise BadRequest(
            f"时长 {value}s 不是模型 {spec.label} 支持的档位，可选: {options}",
            details={"field": "durationSec", "options": options},
        )
    return value


def resolve_aspect_ratio(spec: ModelSpec, aspect_ratio: str) -> str:
    """按比例 schema 校验；schema 没声明时不限制。"""
    options = _param(spec, "aspectRatio").get("options")
    if isinstance(options, (list, tuple)) and options:
        allowed = [str(item) for item in options]
        if aspect_ratio not in allowed:
            raise BadRequest(
                f"比例 {aspect_ratio} 不被模型 {spec.label} 支持，可选: {', '.join(allowed)}",
                details={"field": "aspectRatio", "options": allowed},
            )
    return aspect_ratio


def _param(spec: ModelSpec, name: str) -> dict[str, Any]:
    raw = spec.param_schema.get(name)
    return dict(raw) if isinstance(raw, dict) else {}


def _as_int(value: Any, *, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def resolve_style_preset(spec: ModelSpec, style_preset: str | None) -> str | None:
    """按 `stylePreset` schema 校验风格预设。

    原来这个字段是**第三条路**：契约里声明了、库里落了、`params["stylePreset"]`
    原样发给供应商 —— 既不问模型支不支持，也不校验取值。模型没声明这个参数时，
    等于把一个自己编的键塞进请求体（上游要么忽略、要么 400，两种都不可控）。

    现在的口径与「未声明的参数键一律 400」一致（生图那条链就是这么做的）：
    模型声明了就按它的 `options` 校验，没声明就明确拒绝，而不是默默带过去。
    """
    value = (style_preset or "").strip()
    if not value:
        return None

    schema = _param(spec, "stylePreset")
    if not schema:
        raise BadRequest(
            f"模型 {spec.label} 没有声明风格预设（stylePreset），不能指定风格",
            details={"field": "stylePreset"},
        )
    options = schema.get("options")
    if isinstance(options, (list, tuple)) and options:
        allowed = [str(item) for item in options]
        if value not in allowed:
            raise BadRequest(
                f"风格 {value!r} 不被模型 {spec.label} 支持，可选: {', '.join(allowed)}",
                details={"field": "stylePreset", "options": allowed},
            )
    return value


def resolve_billing_mode(spec: ModelSpec, billing_mode: str | None) -> str:
    """按 `billingMode` schema 校验计费模式。

    与风格预设同一条纪律：**模型没声明就明确拒绝**，而不是默默带过去。
    未声明的模型只有一条上游队列，传一个它不认识的计费模式过去，要么被
    上游忽略（用户以为切了积分、实际还在免费队列），要么被上游 400。

    没传时用 schema 的默认值；schema 也没声明默认就按 `free`——保守的那
    一侧：排慢的免费队列不会误扣积分。
    """
    schema = _param(spec, "billingMode")
    if not schema:
        if billing_mode and billing_mode.strip():
            raise BadRequest(
                f"模型 {spec.label} 没有声明计费模式（billingMode），不能指定计费方式",
                details={"field": "billingMode"},
            )
        return "free"

    value = (billing_mode or "").strip()
    if not value:
        default = str(schema.get("default") or "free")
        options = schema.get("options")
        allowed = [str(item) for item in options] if isinstance(options, (list, tuple)) else []
        return default if default in allowed else (allowed[0] if allowed else default)

    options = schema.get("options")
    if isinstance(options, (list, tuple)) and options:
        allowed = [str(item) for item in options]
        if value not in allowed:
            raise BadRequest(
                f"计费模式 {value!r} 不被模型 {spec.label} 支持，可选: {', '.join(allowed)}",
                details={"field": "billingMode", "options": allowed},
            )
    return value


__all__ = [
    "resolve_aspect_ratio",
    "resolve_billing_mode",
    "resolve_duration",
    "resolve_model",
    "resolve_style_preset",
]

