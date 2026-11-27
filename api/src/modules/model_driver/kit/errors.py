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

from infra.http_errors import (
    DEFAULT_4XX,
    DEFAULT_5XX,
    classify_body_status,
    classify_http_error,
    extract_code,
    extract_detail,
    suffix,
)

__all__ = (
    "DEFAULT_4XX",
    "DEFAULT_5XX",
    "classify_body_status",
    "classify_http_error",
    "extract_code",
    "extract_detail",
    "suffix",
)
