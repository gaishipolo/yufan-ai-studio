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

from collections.abc import Mapping
from typing import Any

from core.exceptions import BadRequest
from core.param_schema import resolve_params
from infra.ai.base import Capability, ModelSpec
from infra.ai.router import ModelRouter

#: 这些参数在 `CreateTaskIn` 里有**各自的顶层字段**（`durationSec` /
#: `aspectRatio` / `stylePreset` / `billingMode`），不经过自由 `params`。
#:
#: 为什么单独列出来：它们各自有专门的校验口径（时长要按 step 判档位、
#: 计费模式要按队列语义收敛），走自由 `params` 会被当成「另一个真相」。
#: 两份真相的后果是「顶层改了、params 里没改」，而重试读的是 params 那份。
TOP_LEVEL_KEYS = frozenset({"durationSec", "aspectRatio", "stylePreset", "billingMode"})


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


def resolve_extra_params(
    spec: ModelSpec, params: Mapping[str, Any] | None
) -> dict[str, Any]:
    """校验并收敛「模型声明的其余参数」，返回要落进 `params_json` 的那一份。

    ## 为什么要有这条通道

    以前 `/tasks` 的参数只有四个写死的顶层键（时长 / 画幅 / 风格 / 计费），
    于是前端也只有两个选择：写死一套表单（模型一改就 400），或者把声明的
    参数全塞进提示词（供应商收到的是一段文字，不是参数）。工作台写死表单
    的直接后果是**提交必定 400**：它无条件带上 `stylePreset`，而所有模型
    都没声明这个参数。

    ## 口径与生图一致

    键必须来自模型 `param_schema`，**未声明的键 400**：打错字（`szie`）被
    静默忽略的话，用户改的那个值从来没生效过，页面看起来却一切正常。

    顶层那几个键（`TOP_LEVEL_KEYS`）**不进这里**：它们各有专门的字段与
    校验口径。两份真相的后果是「顶层改了、params 里没改」，而重试读的
    是 params 那一份 —— 用户第一次选的时长和重试时的时长可能不一样。

    ## 值的收敛

    交给 `core.param_schema.resolve_params` —— 与生图那条链**同一份**代码。
    两份实现一旦漂移，症状是「同一个模型在两条路径上接受不同的值」。
    """
    values = {key: value for key, value in (params or {}).items()}

    duplicated = sorted(key for key in values if key in TOP_LEVEL_KEYS)
    if duplicated:
        # 不是「顶层优先」而是直接拒绝：顶层那几个键有专门的字段，
        # 同一个值出现在两处时语义没法确定（首提与重试可能读不同的那份）。
        raise BadRequest(
            f"参数 {'、'.join(duplicated)} 有专门的字段，不要放进 params",
            details={"field": "params", "duplicatedParams": duplicated},
        )

    schema = dict(spec.param_schema or {})
    if not schema:
        # 模型一个参数都没声明 = 没有契约可依。此时「未声明键一律拒绝」会连
        # 下游自己领域的历史参数一起打死，而它们恰恰是这个模型唯一接受的
        # 参数。原样透传，交给供应商判断（与 `resolve_params` 同一口径）。
        return dict(values)

    unknown = sorted(key for key in values if key not in schema)
    if unknown:
        raise BadRequest(
            f"模型 {spec.label!r} 不接受参数 {unknown}，"
            f"可用参数: {sorted(schema) or '无'}",
            details={"unknownParams": unknown, "availableParams": sorted(schema)},
        )

    nulls = sorted(key for key, value in values.items() if value is None)
    if nulls:
        # `null` 不是「没传」：传了 null 的参数被静默丢掉的话，用户以为改了
        # 那个值而它其实没生效，而页面上看起来一切正常。
        raise BadRequest(
            f"参数 {'、'.join(nulls)} 不能为 null",
            details={"field": "params", "nullParams": nulls},
        )

    # 只对**用户真给了值**的键做收敛：把 schema 收窄成这些键，
    # `resolve_params` 就不会把「没给值但有默认值」的键也补进来 ——
    # 那些值本来就该由模型决定，不该以「用户提交过」的名义落库
    #（落库就意味着重试时它是个显式选择，而用户当时并没有选）。
    subset = {key: dict(schema[key]) for key in values}  # type: ignore[misc]
    return dict(resolve_params(spec.id, subset, values))


__all__ = [
    "TOP_LEVEL_KEYS",
    "resolve_aspect_ratio",
    "resolve_billing_mode",
    "resolve_duration",
    "resolve_extra_params",
    "resolve_model",
    "resolve_style_preset",
]

