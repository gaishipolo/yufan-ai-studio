"""按模型自声明的 `param_schema` 校验并收敛生成参数。

## 为什么在 `core/`

生图接口（`modules/image`）与统一生成门面（`modules/model_driver`）要做的是
同一件事，而模块之间不许互相 import。两份实现一旦漂移，症状是「同一个模型在
两条路径上接受不同的值」——用户照着生图页的提示填参数，换到统一门面就 400，
而两边都自称按 schema 工作。

## 规则全部来自 schema

这里不认识任何具体字段名：模型多声明一个 `quality` 或 `seed`，受理侧自动跟上。
未声明的键**直接拒绝**（打错字 `szie` 被静默忽略的话，用户改的那个值从来没
生效过，页面看起来却一切正常）。

供应商侧兜底不算数：Agnes 对 `1024×1536`（全角乘号）会 400，但那是**异步**
才发生的——用户先看到「生成中」，几秒后只剩一个失败状态，原因藏在
`errorMessage` 里没人看。受理时报错更快，还能把可选值一并告诉他。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.exceptions import BadRequest

_TRUE_STRINGS = {"true", "1", "yes", "on", "是"}
_FALSE_STRINGS = {"false", "0", "no", "off", "否"}

__all__ = ["resolve_params"]


def _options_of(field: dict[str, object]) -> list[object]:
    raw = field.get("options")
    return list(raw) if isinstance(raw, (list, tuple)) else []


def _bad(model_id: str, key: str, message: str, **details: object) -> BadRequest:
    return BadRequest(
        f"模型 {model_id!r} 的参数 {key!r} {message}",
        details={"field": key, **details},
    )


def _coerce(
    model_id: str, key: str, field: dict[str, object], raw: object
) -> object:
    """把前端传来的值按 schema 收敛成供应商能吃的类型，非法值直接 400。

    逐类型校验而不是「照原样透传」：`{"count": "4"}`、`{"count": 2.5}` 到了
    供应商侧可能被静默截断（Agnes 的 n 只认 1），用户以为拿到了 4 张。
    """
    kind = str(field.get("type") or "").strip().lower()
    options = _options_of(field)
    low, high = field.get("min"), field.get("max")

    if kind == "enum" or (not kind and options):
        allowed = [str(item) for item in options]
        text = str(raw)
        if text not in allowed:
            hint = "（注意是英文字母 x，不是乘号 ×）" if key == "size" else ""
            raise _bad(model_id, key, f"不支持，可选: {allowed}{hint}", options=allowed)
        # 回填 options 里的原始类型：数字选项不能变成字符串，供应商按数字校验
        for item in options:
            if str(item) == text:
                return item
        return text

    if kind in ("int", "integer") or (not kind and (low is not None or high is not None)):
        if isinstance(raw, bool):
            raise _bad(model_id, key, f"需要整数，收到 {raw!r}")
        try:
            number = int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise _bad(model_id, key, f"需要整数，收到 {raw!r}") from None
        minimum = 1 if low is None else int(low)
        maximum = None if high is None else int(high)
        if number < minimum or (maximum is not None and number > maximum):
            limit = f"{minimum}-{maximum}" if maximum is not None else f"≥{minimum}"
            raise _bad(
                model_id, key, f"超出范围（{limit}），收到 {number}", min=minimum, max=maximum
            )
        step = field.get("step")
        if step is not None and int(step) > 1 and (number - minimum) % int(step):
            ladder = list(range(minimum, (maximum or number) + 1, int(step)))
            if maximum is not None and (not ladder or ladder[-1] != maximum):
                ladder.append(maximum)
            raise _bad(model_id, key, f"不是支持的档位，可选: {ladder}", options=ladder)
        return number

    if kind in ("float", "number"):
        if isinstance(raw, bool):
            raise _bad(model_id, key, f"需要数字，收到 {raw!r}")
        try:
            number = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise _bad(model_id, key, f"需要数字，收到 {raw!r}") from None
        if (low is not None and number < float(low)) or (high is not None and number > float(high)):
            raise _bad(model_id, key, f"超出范围（{low}-{high}），收到 {number}")
        return number

    if kind in ("bool", "boolean"):
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in _TRUE_STRINGS:
            return True
        if text in _FALSE_STRINGS:
            return False
        raise _bad(model_id, key, f"需要布尔值，收到 {raw!r}")

    if kind in ("string", "text", "str"):
        text = str(raw)
        max_length = field.get("maxLength")
        if max_length is not None and len(text) > int(max_length):
            raise _bad(model_id, key, f"长度不能超过 {int(max_length)}")
        return text

    # 没声明 type 也没有 options/min/max：供应商自有约定，原样透传
    return raw


def resolve_params(
    model_id: str,
    schema: Mapping[str, Any],
    values: Mapping[str, Any] | None = None,
    *,
    top_level: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """按 schema 校验入参，返回**收敛后**的最终参数。

    `top_level` 是接口上的历史顶层字段（生图接口的 `size` / `count`）：同一份
    请求里两处都写时，顶层非空值优先——语义必须有确定答案，否则重试与首提
    可能用不同的值。值为空（`""` / `0` / `None`）时回退到 `values`。
    """
    values = values or {}
    top_level = top_level or {}

    if not schema:
        # 模型一个参数都没声明 = 没有契约可依。此时「未声明键一律拒绝」会连
        # 下游自己领域的历史参数（视频的 `durationSec` 等）一起打死，而它们
        # 恰恰是这个模型唯一接受的参数。原样透传，交给供应商判断。
        return dict(values)

    unknown = [key for key in values if key not in schema]
    if unknown:
        raise BadRequest(
            f"模型 {model_id!r} 不接受参数 {unknown}，可用参数: {list(schema) or '无'}",
            details={"unknownParams": unknown, "availableParams": list(schema)},
        )

    resolved: dict[str, object] = {}
    for key, raw_field in schema.items():
        field = dict(raw_field) if isinstance(raw_field, dict) else {}
        raw = top_level.get(key) if top_level.get(key) else values.get(key)
        if raw is None:
            if "default" not in field:
                # 没给值也没有默认值：不下发，让供应商用自己的默认，别编一个
                continue
            raw = field["default"]
        resolved[key] = _coerce(model_id, key, field, raw)
    return resolved
