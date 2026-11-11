"""Agnes Video 2.5 Flash 驱动：请求形状、轮询语义、错误分类、目录可见性。

## 为什么钉「请求形状」

上游对 Flash 的校验是硬性的：`size` 只能是 `"720P"`、`seconds` 必须是
`"4"`-`"12"` 的**字符串**、`mode` 必须显式给。写错任一处在生产上都是
「用户点了生成 → 异步失败 → 只看到一句上游英文」。

## 为什么钉「错误分类」

计费口径全压在异常类型上：`BadRequest` 不重试，`UpstreamError` / `RateLimited`
才退避重试。实测队列满返回 **503 + `video_queue_full`**——如果把它归成
`BadRequest`，用户会看到「任务失败」，而实际上等半分钟就能提交成功。

## 为什么钉「错误体解析」

文档写 `{"detail": "..."}`，实测是 `{"code","message","data":{"param"}}`，
查询接口又是 `{"error":{"message"}}`。三套都要能读出人话，否则排查时只有
一句「参数校验失败」，连是哪个参数都不知道。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from core.exceptions import (
    BadRequest,
    Unauthorized,
    UpstreamError,
)
from infra.ai.base import Capability, GenerationRequest, GenerationState
from modules.model_driver.drivers.agnes_video import (
    MODEL_ID,
    AgnesVideoDriver,
    AgnesVideoSettings,
)

_BASE = "https://agnes.example"


def _driver(
    handler: Any,
    *,
    base_url: str = _BASE,
    api_key: str = "sk-test-key",
) -> AgnesVideoDriver:
    settings = AgnesVideoSettings(api_key=api_key, base_url=base_url, timeout_sec=5.0)
    return AgnesVideoDriver(settings=settings, transport=httpx.MockTransport(handler))


def _request(**overrides: Any) -> GenerationRequest:
    base: dict[str, Any] = {
        "capability": Capability.VIDEO,
        "model": MODEL_ID,
        "prompt": "雨后的城市街道，霓虹倒影，镜头缓慢推进",
        "params": {"durationSec": 5, "aspectRatio": "16:9"},
    }
    base.update(overrides)
    return GenerationRequest(**base)


def _no_network(request: httpx.Request) -> httpx.Response:  # pragma: no cover
    raise AssertionError(f"这一步不该发出请求：{request.url}")


# ------------------------------------------------------------------ 提交形状
async def test_submit_posts_documented_payload() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content)
        seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"id": "task_1", "video_id": "vid_1"})

    task_id = await _driver(handler).submit(_request())

    assert task_id == "vid_1", "查询要用 video_id，不是 id"
    assert seen["url"] == f"{_BASE}/v1/videos"
    payload = seen["payload"]
    assert payload["model"] == MODEL_ID
    assert payload["seconds"] == "5", "上游要字符串，传整数会被拒"
    assert payload["size"] == "720P", "Flash 只认 720P"
    assert payload["mode"] == "text"
    assert payload["aspect_ratio"] == "16:9"
    assert "images" not in payload, "文生视频不能带 images"
    assert seen["authorization"] == "Bearer sk-test-key"


async def test_base_url_with_v1_suffix_does_not_double_the_prefix() -> None:
    """文档给的基址带 `/v1`，现役 `.env` 不带；两种写法都要能用。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"video_id": "vid_1"})

    await _driver(handler, base_url="https://agnes.example/v1").submit(_request())

    assert seen["url"] == f"{_BASE}/v1/videos"


async def test_reference_urls_switch_to_reference_mode() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"video_id": "vid_1"})

    await _driver(handler).submit(
        _request(reference_urls=("https://cdn.example/char.png",))
    )

    assert seen["payload"]["mode"] == "reference"
    assert seen["payload"]["images"] == ["https://cdn.example/char.png"]


async def test_non_public_reference_is_rejected_before_request() -> None:
    """Agnes 服务端自己去拉图：内网地址/对象 key 交上去只会在提交后失败。"""
    with pytest.raises(BadRequest) as exc:
        await _driver(_no_network).submit(
            _request(reference_urls=("library/assets/abc.png",))
        )

    assert "公网" in str(exc.value)


async def test_more_than_five_references_is_rejected() -> None:
    urls = tuple(f"https://cdn.example/{index}.png" for index in range(6))

    with pytest.raises(BadRequest) as exc:
        await _driver(_no_network).submit(_request(reference_urls=urls))

    assert "5 张" in str(exc.value)


# -------------------------------------------------------------- 参数前置校验
async def test_duration_outside_flash_range_is_rejected_before_request() -> None:
    """通用门面不校验 param_schema，这道检查是唯一的护栏。"""
    with pytest.raises(BadRequest) as exc:
        await _driver(_no_network).submit(
            _request(params={"durationSec": 20, "aspectRatio": "16:9"})
        )

    assert "4-12" in str(exc.value)
    assert exc.value.details == {"field": "durationSec", "min": 4, "max": 12}


async def test_unknown_aspect_ratio_is_rejected_before_request() -> None:
    with pytest.raises(BadRequest) as exc:
        await _driver(_no_network).submit(
            _request(params={"durationSec": 5, "aspectRatio": "2:1"})
        )

    assert "21:9" in str(exc.value), "报错要给出可选值，否则用户只能猜"


async def test_missing_api_key_fails_before_request() -> None:
    with pytest.raises(UpstreamError) as exc:
        await _driver(_no_network, api_key="  ").submit(_request())

    assert "AGNES_API_KEY" in str(exc.value)


async def test_non_video_capability_is_refused() -> None:
    with pytest.raises(BadRequest) as exc:
        await _driver(_no_network).submit(_request(capability=Capability.IMAGE))

    assert "视频" in str(exc.value)


# -------------------------------------------------------------------- 轮询
async def test_poll_queries_agnesapi_with_model_name() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "progress": 100,
                "url": "https://cdn.example/v.mp4",
                "internal_status": "pending",
            },
        )

    progress = await _driver(handler).poll("vid_1", model=MODEL_ID)

    assert seen["path"] == "/agnesapi", "查询在根路径上，不在 /v1 下"
    assert seen["params"] == {"video_id": "vid_1", "model_name": MODEL_ID}
    assert progress.state is GenerationState.SUCCEEDED
    assert progress.progress == 100
    assert progress.output_url == "https://cdn.example/v.mp4"


async def test_poll_accepts_label_as_model() -> None:
    """历史记录里可能只存了展示名；带错 model_name 上游会查不到任务。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"status": "running", "progress": 40})

    await _driver(handler).poll("vid_1", model="Agnes Video 2.5 Flash")

    assert seen["params"]["model_name"] == MODEL_ID


async def test_poll_running_keeps_progress() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "running", "progress": 42})

    progress = await _driver(handler).poll("vid_1", model=MODEL_ID)

    assert progress.state is GenerationState.RUNNING
    assert progress.progress == 42


async def test_poll_completed_without_url_fails_loudly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "completed", "progress": 100})

    progress = await _driver(handler).poll("vid_1", model=MODEL_ID)

    assert progress.state is GenerationState.FAILED
    assert progress.retryable is False
    assert progress.error and "没有返回视频地址" in progress.error


async def test_poll_failed_carries_upstream_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "failed",
                "progress": 30,
                "error": {"code": "content_blocked", "message": "内容不合规"},
            },
        )

    progress = await _driver(handler).poll("vid_1", model=MODEL_ID)

    assert progress.state is GenerationState.FAILED
    assert progress.error == "内容不合规", "上游原因要原样带给用户"


async def test_poll_unknown_status_is_treated_as_running() -> None:
    """上游加新状态时宁可继续等（有 30 分钟墙钟兜底），也不要误判失败。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "queued_preparing", "progress": 5})

    progress = await _driver(handler).poll("vid_1", model=MODEL_ID)

    assert progress.state is GenerationState.RUNNING


async def test_poll_missing_task_is_terminal_not_retried() -> None:
    """404 = 任务号无效/已过期。抛异常会让框架一直重试到超时。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"error": {"code": 404, "message": "任务不存在"}}
        )

    progress = await _driver(handler).poll("vid_missing", model=MODEL_ID)

    assert progress.state is GenerationState.FAILED
    assert progress.retryable is False
    assert progress.error and "查不到" in progress.error


async def test_empty_external_id_is_refused() -> None:
    with pytest.raises(BadRequest):
        await _driver(_no_network).poll("  ", model=MODEL_ID)


# ------------------------------------------------------------------ 错误分类
async def test_queue_full_is_retryable_and_not_treated_as_delivered() -> None:
    """实测：503 + code=video_queue_full。

    这一条必须归 `RateLimited`（429），不能归 `UpstreamError`（502）：
    runner 的 `_possibly_delivered` 把 5xx 一律当「请求可能已送达」，
    结果是任务直接失败、禁止自动重试，还把原因盖成「提交结果未知」。
    """
    from core.exceptions import RateLimited
    from generation.runner import _possibly_delivered

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "code": "video_queue_full",
                "message": "视频队列已满，请稍后重试 (request id: abc)",
                "data": None,
            },
        )

    with pytest.raises(RateLimited) as exc:
        await _driver(handler).submit(_request())

    assert exc.value.retryable is True, "队列满是临时状态，必须让框架退避重试"
    assert _possibly_delivered(exc.value) is False, "上游明确没建任务，重试不会二次计费"
    assert "队列已满" in str(exc.value), "要把上游原因带给用户"
    assert "video_queue_full" in str(exc.value), "业务码要留痕，便于对账"


async def test_other_5xx_stays_possibly_delivered() -> None:
    """网关自己 502（不是队列满）时仍按「可能已受理」保守处理，不许自动重提。"""
    from core.exceptions import UpstreamError
    from generation.runner import _possibly_delivered

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    with pytest.raises(UpstreamError) as exc:
        await _driver(handler).submit(_request())

    assert _possibly_delivered(exc.value) is True


async def test_invalid_request_names_the_param() -> None:
    """实测 400 是 `{code, message, data:{param}}`，不是文档里的 `{detail}`。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "code": "invalid_request",
                "message": "size 必须是 720P 之一",
                "data": {"param": "size", "valid_sizes": ["720P"]},
            },
        )

    with pytest.raises(BadRequest) as exc:
        await _driver(handler).submit(_request())

    assert exc.value.retryable is False
    assert "size" in str(exc.value)


async def test_auth_failure_is_explicit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"code": 401, "message": "Invalid token"}}
        )

    with pytest.raises(Unauthorized) as exc:
        await _driver(handler).submit(_request())

    assert "AGNES_API_KEY" in str(exc.value)
    assert exc.value.retryable is False


async def test_cancel_refuses_instead_of_pretending() -> None:
    """假装取消会让本地显示已取消、上游照常出片计费。"""
    with pytest.raises(BadRequest) as exc:
        await _driver(_no_network).cancel("vid_1")

    assert "控制台" in str(exc.value)


# -------------------------------------------------------------- 标准符合性
def test_driver_passes_contract_validation() -> None:
    from modules.model_driver.spec import validate_driver

    driver = AgnesVideoDriver()
    assert validate_driver(driver) == []
    assert driver.name == "agnes_video"
    assert driver.supports_poll is True, "异步供应商必须显式声明，否则恢复器会判死在途任务"
    assert driver.capabilities == frozenset({Capability.VIDEO})


def test_declared_model_matches_upstream_limits() -> None:
    """目录里声明的边界必须和上游一致：写宽了是「选了才被拒」，写窄了是白丢档位。"""
    spec = AgnesVideoDriver.models[0]

    assert spec.id == MODEL_ID
    assert spec.provider == "agnes_video"
    assert spec.capability is Capability.VIDEO
    assert spec.max_references == 0, "视频任务没有解析参考图的通道，别画上传框"

    duration = spec.param_schema["durationSec"]
    assert (duration["min"], duration["max"], duration["default"]) == (4, 12, 5)
    assert duration["step"] == 1, (
        "缺 step 时前端按 5 秒跳展开档位，4-12 只会出现 4/9/12 三档，中间秒数选不到"
    )

    aspect = spec.param_schema["aspectRatio"]
    assert aspect["default"] == "16:9"
    assert aspect["options"] == ["9:16", "16:9", "1:1"], (
        "只能列平台 AspectRatio 枚举支持的三种；21:9/4:3/3:4 上游支持但提交会 422"
    )


async def test_extra_upstream_ratios_still_pass_through() -> None:
    """平台枚举只有三种，但驱动按上游全量校验：将来直连时不用再改这里。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"video_id": "vid_1"})

    await _driver(handler).submit(_request(params={"durationSec": 5, "aspectRatio": "21:9"}))

    assert seen["payload"]["aspect_ratio"] == "21:9"


def test_driver_is_auto_loaded_and_model_is_listed(client: Any) -> None:
    """核心诉求：丢进 `drivers/` 就能在生成工作台的模型下拉里看到。

    不写这条的话，驱动加载失败的典型症状是「下拉里少一个模型」，
    而不是启动失败——没人会去翻日志。
    """
    from infra import get_model_router
    from modules.model_driver.registry import driver_summaries, load_errors

    assert not load_errors(), load_errors()
    infos = {item.name: item for item in driver_summaries()}
    assert infos["agnes_video"].loaded is True
    assert infos["agnes_video"].model_count == 1

    router = get_model_router()
    public = [spec.id for spec in router.public_models(Capability.VIDEO)]
    assert MODEL_ID in public, "internal=True 会让它从下拉里消失"
    assert router.provider_for(MODEL_ID).name == "agnes_video"
