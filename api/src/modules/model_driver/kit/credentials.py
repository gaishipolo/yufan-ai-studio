"""供应商凭据的前置检查：**缺密钥只有一个判定口径**。

## 为什么这是 kit 件（而不是各驱动自己写）

2026-11-23 的事故（设计文档 §2.4）就是「同一个语义有两处真相」的直接后果：

```
error_message = 提交结果未知：请求已发出但未确认供应商是否受理，为避免重复计费未自动重试
external_id = None, submit_attempts = 1
```

真实原因：**`GRSAI_API_KEY` 是空的，请求根本没发出去**。但框架在
`generation/runner.py::_possibly_delivered` 按 HTTP 语义分类：

- 4xx → 确定没送达 → 允许安全重试，并把上游原因原样透给用户
- 5xx → 可能已受理 → 改写成上面那句「提交结果未知」

「缺 key 抛哪种异常」原本**由每个驱动自己决定**：`grsai` / `bailian` 抛
`BadRequest`（4xx，口径正确），`agnes` / `agnes_video` 抛 `UpstreamError`
（5xx，落进「可能已受理」那一侧）—— 用户被引导去核对一个上游根本不存在的任务。

## 口径

**缺密钥 = 请求根本没发出 = 确定没送达 = 4xx（`BadRequest`）。**

这不是「重试就好了」，而是「用户必须去配 key」—— 4xx 侧框架允许安全重试，
重试前会拿到这条真实原因，不会被改写成误导文案。

## 为什么文案由驱动给

异常**类型**必须只有一处判定（否则必然漂移）；但文案带各驱动的上下文
（百炼是「北京地域」、agnes_video 是「与生图共用」），强行统一会丢信息。
所以：类型由本模块定，文案由驱动通过 `note` 补充。
"""

from __future__ import annotations

from core.exceptions import BadRequest


def require_api_key(
    value: str | None,
    *,
    vendor: str,
    env_name: str,
    note: str = "",
) -> str:
    """取供应商密钥；没配就抛 `BadRequest`（4xx）。

    Args:
        value: 配置里的密钥原值（可能是空串 / 纯空格 / None）
        vendor: 供应商名，用于文案（"Agnes"）
        env_name: 环境变量名，用于文案（"AGNES_API_KEY"）
        note: 补充说明，可选（"北京地域" / "与生图共用"）

    Returns:
        去掉首尾空白的密钥（调用方拿到的就是可直接放进请求头的值）

    Raises:
        BadRequest: 密钥为空 —— **不抛 5xx**，否则会被框架改写成
            「可能已受理」的错误文案（2026-11-23 事故）。
    """
    key = (value or "").strip()
    if key:
        return key
    detail = f"{vendor} 未配置：请设置 {env_name}"
    if note:
        detail += f"（{note}）"
    raise BadRequest(detail)


__all__ = ["require_api_key"]
