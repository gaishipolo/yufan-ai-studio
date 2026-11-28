"""阶段 0 黄金样本：kit/errors.py 能还原 4 个驱动各自的分类口径。

本测试**不动生产代码**（驱动还在用自己的函数），只证明一件事：框架的唯一
执行件配上驱动声明的映射表，能逐条还原各自现有行为 —— 这是后续把驱动
切到 kit 的前提（A1 验证失败后的降级方案：G5 = 同语义同状态码，不是同一函数）。
"""

from __future__ import annotations

import httpx
import pytest

from core.exceptions import BadRequest, Forbidden, RateLimited, Unauthorized, UpstreamError
from modules.model_driver.kit.errors import (
    classify_body_status,
    classify_http_error,
    suffix,
)


def response(status: int, *, body: dict | None = None, text: str = "") -> httpx.Response:
    """构造上游响应。body 与 text 二选一（httpx 同时传会让 json() 取不到 body）。"""
    if body is not None:
        return httpx.Response(status, json=body, request=httpx.Request("GET", "https://upstream"))
    return httpx.Response(
        status,
        text=text or "upstream error",
        request=httpx.Request("GET", "https://upstream"),
    )


# 四个驱动的实际口径（逐行比对所得，设计文档 §1.1 A1）
AGNES_MAP = {
    401: Unauthorized,
    403: Forbidden,
    408: BadRequest,
    429: RateLimited,
}
AGNES_VIDEO_MAP = {**AGNES_MAP}
AGNES_VIDEO_OVERRIDES = {(503, "video_queue_full"): RateLimited}
# grsai 的口径：4xx（含 408）全 BadRequest、5xx UpstreamError。
# 408 必须**显式声明**覆盖 kit 的默认（默认按「可能已受理」归 5xx 侧）——
# 这正是「决定权在驱动、实现只有一份」的意义。
GRSAI_MAP: dict[int, type[Exception]] = {408: BadRequest}
BAILIAN_MAP: dict[int, type[Exception]] = {}  # bailian 4xx 除 408，408 走兜底 5xx


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, Unauthorized),
        (403, Forbidden),
        (404, BadRequest),
        (429, RateLimited),
        (400, BadRequest),
        (500, UpstreamError),
        (408, BadRequest),  # 408 是明确没受理，与其它 4xx 同侧
    ],
)
def test_agnes_mapping(status: int, expected: type[Exception]) -> None:
    exc = classify_http_error(response(status), vendor="Agnes", status_map=AGNES_MAP)
    assert isinstance(exc, expected)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, Unauthorized),
        (429, RateLimited),
        (500, UpstreamError),
    ],
)
def test_agnes_video_mapping(status: int, expected: type[Exception]) -> None:
    exc = classify_http_error(
        response(status),
        vendor="Agnes Video",
        status_map=AGNES_VIDEO_MAP,
        code_overrides=AGNES_VIDEO_OVERRIDES,
    )
    assert isinstance(exc, expected)


def test_agnes_video_business_code_override() -> None:
    """agnes_video 的实质差异：5xx + video_queue_full → 可重试 429。"""
    exc = classify_http_error(
        response(503, body={"code": "video_queue_full", "message": "队列满"}),
        vendor="Agnes Video",
        status_map=AGNES_VIDEO_MAP,
        code_overrides=AGNES_VIDEO_OVERRIDES,
    )
    assert isinstance(exc, RateLimited)
    assert "video_queue_full" not in str(exc) or "队列满" in str(exc)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, BadRequest),  # grsai 把 401 降为 400
        (403, BadRequest),
        (429, BadRequest),
        (408, BadRequest),  # grsai 兜底也是 400（与 agnes 不同）
        (503, UpstreamError),
    ],
)
def test_grsai_mapping(status: int, expected: type[Exception]) -> None:
    exc = classify_http_error(response(status), vendor="Grsai", status_map=GRSAI_MAP)
    assert isinstance(exc, expected)


def test_code_override_priority_over_status_map() -> None:
    """例外表优先级高于状态码表 —— 否则 agnes_video 的规则表达不出来。"""
    exc = classify_http_error(
        response(503, body={"code": "video_queue_full"}),
        vendor="Agnes Video",
        status_map={503: UpstreamError},
        code_overrides={(503, "video_queue_full"): RateLimited},
    )
    assert isinstance(exc, RateLimited)


def test_unknown_code_falls_back_to_status_map() -> None:
    exc = classify_http_error(
        response(503, body={"code": "other"}),
        vendor="Agnes Video",
        status_map={503: UpstreamError},
        code_overrides={(503, "video_queue_full"): RateLimited},
    )
    assert isinstance(exc, UpstreamError)


def test_detail_and_suffix() -> None:
    """两个 _suffix 逐字等价：非空加中文冒号，空则空串。"""
    assert suffix("boom") == "：boom"
    assert suffix("") == ""
    exc = classify_http_error(
        response(400, body={"error": "参数错了"}), vendor="Grsai"
    )
    assert isinstance(exc, BadRequest)
    assert "参数错了" in str(exc)


def test_body_status_failed_is_client_side() -> None:
    """上游 200 + status=failed = 明确拒绝 → 4xx（不是 5xx）。"""
    exc = classify_body_status(
        vendor="Grsai", status="failed", status_map={"failed": BadRequest}, error="generate failed"
    )
    assert isinstance(exc, BadRequest)
    assert exc.http_status < 500


def test_body_status_unknown_returns_none() -> None:
    assert (
        classify_body_status(vendor="Grsai", status="running", status_map={"failed": BadRequest})
        is None
    )
