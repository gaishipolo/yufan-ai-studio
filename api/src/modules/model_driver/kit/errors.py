"""HTTP 错误分类的**唯一实现** + 错误文案后缀。

## 为什么分类实现只有一份，映射表却各驱动不同

逐行比对（2026-11-23，设计文档 §1.1 A1）证明：4 个驱动的错误映射规则
**互不相同** —— 429 在 agnes 是 `RateLimited(429)`、grsai 是 `BadRequest(400)`；
401/403 同理；agnes_video 还有 `5xx + video_queue_full → RateLimited(429)` 的
业务码例外。这些差异是**业务语义**，不能抹平。

但「按状态码查表 → 取错误正文 → 拼异常」这段**执行逻辑**是同一件事。原来
4 份实现各写一遍，于是一处改错（2026-11-23 的缺 key 抛 502）就波及一个驱动。
现在框架持唯一实现，驱动只声明**数据**：

```python
HTTP_STATUS_MAP = {401: Unauthorized, 403: Forbidden, 429: RateLimited, ...}
CODE_OVERRIDES = {(503, "video_queue_full"): RateLimited}  # 业务码例外
```

对应 LiteLLM 的做法：统一异常类型 + 按 provider 的映射矩阵，retry/fallback
只看统一异常，不认各家原始错误格式。
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from core.exceptions import AppError, BadRequest, UpstreamError

#: 默认兜底：没在映射表里的 4xx 按 BadRequest、5xx 按 UpstreamError。
#: 驱动可以显式声明别的口径（grsai 的 429 → BadRequest）。
DEFAULT_4XX = BadRequest
DEFAULT_5XX = UpstreamError


def suffix(error: str) -> str:
    """把上游错误正文拼成中文后缀。

    `grsai.py:731` 与 `grsai_video.py:352` 的两个 `_suffix` **逐字等价** ——
    A1 四组比对里唯一确定可抽的件。
    """
    return f"：{error}" if error else ""


def extract_detail(response: httpx.Response) -> str:
    """从响应体里取错误正文，取不到就退回 response.text（截 200 字）。

    这是 4 份实现里**共同的第一步**（形状各异但动作一致）：
    body.error / body.message / 裸 text。
    """
    detail = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        for key in ("error", "message", "detail", "msg"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                detail = value.strip()
                break
    return detail or response.text.strip()[:200] or "（上游没有给错误正文）"


def extract_code(response: httpx.Response) -> str:
    """取顶层业务码（仅字符串），供 `code_overrides` 匹配。"""
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict) and isinstance(body.get("code"), str):
        return body["code"].strip()
    return ""


def classify_http_error(
    response: httpx.Response,
    *,
    vendor: str,
    status_map: Mapping[int, type[AppError]] | None = None,
    code_overrides: Mapping[tuple[int, str], type[AppError]] | None = None,
    detail: str | None = None,
) -> AppError:
    """按驱动声明的映射表把 HTTP 错误分类成平台异常。

    **本函数是分类的唯一实现**，但「什么状态算什么异常」由 `status_map` /
    `code_overrides` 决定 —— 决定权仍在驱动（尊重业务语义），实现只有一份。

    Args:
        response: 上游响应（只用于 status_code 与兜底正文）
        vendor: 供应商名，出现在异常文案里便于定位
        status_map: `{状态码: 异常类}`，缺省用 4xx→BadRequest / 5xx→UpstreamError
        code_overrides: `{(状态码, 业务码): 异常类}` 的例外表，优先级高于 status_map
        detail: 已提取好的错误正文；不传则现场 `extract_detail()`
    """
    status = response.status_code
    text = detail if detail is not None else extract_detail(response)

    exc_cls: type[AppError] | None = None
    if code_overrides:
        code = extract_code(response)
        exc_cls = code_overrides.get((status, code))
    if exc_cls is None and status_map:
        exc_cls = status_map.get(status)
    if exc_cls is None:
        # 408 与 5xx 同侧：请求已发出但没拿到确认，可能已受理 →
        # 框架 _possibly_delivered 按「可能已受理」处理，禁止自动重试。
        # grsai 的 408→BadRequest 口径由它自己的 status_map 显式声明覆盖。
        exc_cls = DEFAULT_5XX if status >= 500 or status == 408 else DEFAULT_4XX

    return exc_cls(f"{vendor} 上游异常（HTTP {status}）{suffix(text)}")


def classify_body_status(
    *,
    vendor: str,
    status: str,
    status_map: Mapping[str, type[AppError]],
    error: str = "",
) -> AppError | None:
    """上游 HTTP 200 但响应体回报失败时的分类（如 `status=failed`）。

    这类失败是「上游明确拒绝、没受理」→ 应落 4xx 侧（允许安全重试），
    不是 5xx。返回 None 表示状态不在失败集合里。
    """
    exc_cls = status_map.get(status)
    if exc_cls is None:
        return None
    return exc_cls(f"{vendor} 提交失败{suffix(error)}")


__all__ = [
    "DEFAULT_4XX",
    "DEFAULT_5XX",
    "classify_body_status",
    "classify_http_error",
    "extract_code",
    "extract_detail",
    "suffix",
]
