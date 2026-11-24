"""各驱动「HTTP 错误 → 框架异常」的当前行为基线（2026-11-24）。

这些映射**故意不统一**：
- Agnes：401→Unauthorized、403→Forbidden、404/400→BadRequest、429→RateLimited，
  其它 4xx→UpstreamError；
- Grsai：一律 4xx（含 408）→BadRequest，5xx→UpstreamError；
- Agnes 视频：比 Agnes 生图多一条**业务码例外**（503 + `video_queue_full`
  →RateLimited）。

这份基线是阶段 1「错误分类收敛到 `kit/errors.py`」迁移前的**等价性标尺**：
迁移后这些断言必须仍然成立，否则就是行为变更 —— 前端按 `code` 与 `message`
展示，异常类一变，HTTP 状态码就跟着变（BadRequest=400 vs UpstreamError=502）。
"""

import httpx
import pytest

from core.exceptions import (
    BadRequest,
    Forbidden,
    RateLimited,
    Unauthorized,
    UpstreamError,
)
from modules.model_driver.drivers import agnes, agnes_video, grsai


@pytest.mark.parametrize(
    ("status", "expected", "expected_http_status"),
    [
        (401, Unauthorized, 401),
        (403, Forbidden, 403),
        (404, BadRequest, 400),
        (429, RateLimited, 429),
        (400, BadRequest, 400),
        (402, UpstreamError, 502),
        (500, UpstreamError, 502),
    ],
)
def test_agnes_maps_http_errors_as_it_does_today(status, expected, expected_http_status):
    err = agnes._http_error(httpx.Response(status, json={"error": {"message": "boom"}}))

    assert isinstance(err, expected)
    assert err.http_status == expected_http_status
    assert "boom" in err.message, "上游原文要带进文案，否则排查时看不到根因"


def test_agnes_reads_the_nested_error_message():
    """Agnes 的错误正文在 `error.message` 里（嵌套一层）。"""
    err = agnes._http_error(
        httpx.Response(400, json={"error": {"message": "参数不对", "code": "E1"}})
    )

    assert "参数不对" in err.message


def test_agnes_video_treats_queue_full_as_rate_limited():
    """Agnes 视频的业务码例外：排队满 → 限流（可重试），不是上游故障。"""
    err = agnes_video._http_error(
        httpx.Response(503, json={"code": "video_queue_full", "message": "排队中"})
    )

    assert isinstance(err, RateLimited)
    assert "video_queue_full" in err.message


def test_agnes_video_treats_other_503_as_upstream_error():
    """503 但不是排队满 → 上游故障。"""
    err = agnes_video._http_error(
        httpx.Response(503, json={"code": "server_busy", "message": "忙"})
    )

    assert isinstance(err, UpstreamError)


@pytest.mark.parametrize(
    ("status", "expected", "expected_http_status"),
    [
        (401, BadRequest, 400),
        (404, BadRequest, 400),
        (429, BadRequest, 400),
        (408, BadRequest, 400),
        (500, UpstreamError, 502),
    ],
)
def test_grsai_maps_every_4xx_to_bad_request(status, expected, expected_http_status):
    """Grsai 的口径：4xx 一律 BadRequest（含 408），5xx 才 UpstreamError。"""
    err = grsai.grsai_http_error(httpx.Response(status, json={"error": "boom"}))

    assert isinstance(err, expected)
    assert err.http_status == expected_http_status


def test_agnes_and_grsai_disagree_on_429_on_purpose():
    """同一个 429，两边映射不同是**业务语义差异**，不是 bug。

    抹平它就等于替供应商决定了「限流算不算可重试」——所以阶段 1 收敛的是
    **实现位置**（都走 kit），不是**判定结果**。
    """
    agnes_err = agnes._http_error(httpx.Response(429, json={"error": {"message": "slow"}}))
    grsai_err = grsai.grsai_http_error(httpx.Response(429, json={"error": "slow"}))

    assert isinstance(agnes_err, RateLimited)
    assert isinstance(grsai_err, BadRequest)


def test_retryability_follows_the_mapped_status():
    """可重试性由映射后的 HTTP 状态决定（429/5xx 可重试，4xx 不可）。"""
    assert agnes._http_error(httpx.Response(429, json={})).retryable is True
    assert agnes._http_error(httpx.Response(500, json={})).retryable is True
    assert agnes._http_error(httpx.Response(404, json={})).retryable is False
    assert agnes._http_error(httpx.Response(401, json={})).retryable is False
