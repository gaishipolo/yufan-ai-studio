"""Grsai 驱动：请求形状、状态映射、参数改名，以及是否符合驱动开发标准。

## 为什么钉「请求形状」

Grsai 的三个系列（nano-banana / gpt-image / wan-video）共用一个入口
`POST /v1/api/generate`，按请求体的 `model` 分流 —— 字段发错不会报「参数名
不对」，只会报一句含糊的 400，或者更糟：出片了但画幅/时长不对。所以用
`httpx.MockTransport` 接在真实 `AsyncClient` 上，测真实请求构造路径。

## 为什么钉「状态映射」

上游的状态是字符串（running / succeeded / failed / violation），映射错了
症状是「任务一直转圈到超时」，或者「失败的任务被当成可重试」——后者会
重复计费。所以这里逐个状态钉住终态与 `retryable`。

## 为什么钉「参数改名」

平台的顶层字段叫 `durationSec`，上游叫 `duration`；平台把 `size` / `count`
从参数里剔掉单独存列（驱动拿不到），所以画幅只能走 `aspectRatio`。这几处
错了都不会立刻报错，只会出片不对。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from core.exceptions import BadRequest, UpstreamError
from infra.ai.base import Capability, GenerationRequest, GenerationState
from modules.model_driver.drivers.grsai import GrsaiImageDriver
from modules.model_driver.drivers.grsai_video import MODELS as VIDEO_MODELS
from modules.model_driver.drivers.grsai_video import GrsaiVideoDriver


@pytest.fixture(autouse=True)
def _grsai_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Grsai 的密钥从 env 读，而配置是缓存的 —— 换 key 必须先清缓存。"""
    # 配置改用 mtime 指纹缓存后，env 变化自动感知 —— 不需要 cache_clear
    monkeypatch.setenv("GRSAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("GRSAI_BASE_URL", "https://grsai.example")
    yield


class _Recorder:
    """记录真实发出的请求，并按预设脚本回响应。"""

    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("上游被调用的次数超出预期")
        return self._responses.pop(0)

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[0].content)

    @property
    def url(self) -> str:
        return str(self.requests[0].url)


def _json(payload: dict[str, Any], *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _image_request(**overrides: Any) -> GenerationRequest:
    base: dict[str, Any] = {
        "capability": Capability.IMAGE,
        "model": "grsai-nano-banana-2",
        "prompt": "一只在草地上奔跑的边牧",
        "params": {"aspectRatio": "16:9", "imageSize": "2K"},
    }
    base.update(overrides)
    return GenerationRequest(**base)


def _video_request(**overrides: Any) -> GenerationRequest:
    base: dict[str, Any] = {
        "capability": Capability.VIDEO,
        "model": "grsai_video-wan-3.0",
        "prompt": "城市雨夜，霓虹灯闪烁",
        "params": {"durationSec": 10, "aspectRatio": "9:16", "resolution": "1080p"},
    }
    base.update(overrides)
    return GenerationRequest(**base)


# ------------------------------------------------------------------ 请求形状
async def test_submit_strips_provider_prefix_and_uses_async() -> None:
    """模型 id 要剥掉 `grsai-` 前缀；回复类型固定 async。"""
    recorder = _Recorder(_json({"id": "task-1", "status": "running"}))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    result = await driver.submit(_image_request())

    assert result == "task-1"
    assert recorder.body["model"] == "nano-banana-2"
    assert recorder.body["replyType"] == "async"
    assert recorder.body["aspectRatio"] == "16:9"
    assert recorder.body["imageSize"] == "2K"
    assert recorder.body["prompt"] == "一只在草地上奔跑的边牧"
    # 平台的历史顶层字段不发给上游（上游不认 `size`）。
    assert "size" not in recorder.body
    assert "count" not in recorder.body
    assert recorder.url == "https://grsai.example/v1/api/generate"


async def test_submit_authorization_header() -> None:
    recorder = _Recorder(_json({"id": "task-1", "status": "running"}))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    await driver.submit(_image_request())

    assert recorder.requests[0].headers["authorization"] == "Bearer sk-test-key"


async def test_submit_gpt_image_passes_quality_and_output_format() -> None:
    """保留的 gpt-image-2.6 仍传 quality / outputFormat；imageSize 不串味。"""
    recorder = _Recorder(_json({"id": "task-2", "status": "running"}))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    await driver.submit(
        _image_request(
            model="grsai-gpt-image-2.6",
            params={
                "aspectRatio": "1:1",
                "quality": "auto",
                "outputFormat": "gif",
                "background": "transparent",
            },
        )
    )

    assert recorder.body["model"] == "gpt-image-2.6"
    assert recorder.body["quality"] == "auto"
    assert recorder.body["outputFormat"] == "gif"
    assert recorder.body["background"] == "transparent"
    assert "imageSize" not in recorder.body


@pytest.mark.parametrize(
    "retired_id",
    ["grsai-gpt-image-2-vip", "grsai-gpt-image-2.6-flare", "grsai-nano-banana-pro-vip"],
)
async def test_retired_model_ids_are_rejected(retired_id: str) -> None:
    """下线的型号连 id 都不能再提交 —— 否则目录裁剪了、老入口仍能绕过。"""
    recorder = _Recorder(_json({"id": "task-3", "status": "running"}))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))
    with pytest.raises(BadRequest, match="不认识模型"):
        await driver.submit(_image_request(model=retired_id))
    assert not recorder.requests, "下线型号不得发出任何上游请求"


async def test_submit_splits_references_and_translates_mixed_refs() -> None:
    """参考图按原顺序发出；`{{Mixed N}}` 翻成「图N」。"""
    recorder = _Recorder(_json({"id": "task-4", "status": "running"}))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    await driver.submit(
        _image_request(
            prompt="让 {{Mixed 1}} 和 {{Mixed 2}} 并肩站立",
            reference_urls=(
                "data:image/png;base64,AAAA",
                "https://cdn.example.com/b.png",
            ),
        )
    )

    assert recorder.body["images"] == [
        "data:image/png;base64,AAAA",
        "https://cdn.example.com/b.png",
    ]
    assert recorder.body["prompt"] == "让 图1 和 图2 并肩站立"


async def test_submit_rejects_more_than_four_references() -> None:
    """生图链不消费 `reference_limits`，张数上限得驱动自己判。"""
    recorder = _Recorder(_json({"id": "task-5", "status": "running"}))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    with pytest.raises(BadRequest, match="最多 4 张参考图"):
        await driver.submit(
            _image_request(
                reference_urls=tuple(
                    f"data:image/png;base64,AAAA{i}" for i in range(5)
                )
            )
        )
    assert recorder.requests == []


async def test_submit_rejects_unrecognizable_reference_format() -> None:
    """认不出格式的素材直接报错，而不是静默跳过。

    跳过的话这份素材不会上传，而提示词里的 `{{Mixed N}}` 编号是按上传顺序
    算的 —— 少一个素材，后面每个编号都指错对象：出片照样成功，只是内容不对。
    """
    recorder = _Recorder(_json({"id": "task-6", "status": "running"}))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    with pytest.raises(BadRequest, match="认不出参考素材的格式"):
        await driver.submit(_image_request(reference_urls=("https://cdn.example.com/a",)))
    assert recorder.requests == []


async def test_submit_rejects_audio_reference_for_image() -> None:
    """生图只收参考图：音频混进来会被剔掉，同样导致提示词编号错位。"""
    recorder = _Recorder(_json({"id": "task-7", "status": "running"}))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    with pytest.raises(BadRequest, match="不支持参考音频"):
        await driver.submit(
            _image_request(reference_urls=("data:audio/mpeg;base64,AAAA",))
        )
    assert recorder.requests == []


async def test_video_submit_rejects_video_reference() -> None:
    """上游的参考视频与首尾帧这套用法当前平台没有入口，明确拒绝。"""
    recorder = _Recorder(_json({"id": "task-v5", "status": "running"}))
    driver = GrsaiVideoDriver(transport=httpx.MockTransport(recorder))

    with pytest.raises(BadRequest, match="暂不支持参考视频"):
        await driver.submit(
            _video_request(reference_urls=("data:video/mp4;base64,AAAA",))
        )
    assert recorder.requests == []


async def test_submit_upstream_reported_failure_is_client_side() -> None:
    """上游在 200 的响应体里明确回报 failed = 它明确拒绝了这次请求（没受理）。

    这种失败落 4xx：框架据此判「确定没送达」，允许安全重试，并把上游给的
    原因给用户；落 5xx 会被改写成「提交结果未知，请核对上游任务」—— 而上游
    根本没收这次请求，用户白跑一趟控制台。
    """
    recorder = _Recorder(
        _json({"id": "task-8", "status": "failed", "error": "generate failed"})
    )
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    with pytest.raises(BadRequest, match="提交失败") as excinfo:
        await driver.submit(_image_request())
    assert excinfo.value.http_status < 500


async def test_video_submit_upstream_reported_failure_is_client_side() -> None:
    recorder = _Recorder(
        _json({"id": "task-v6", "status": "failed", "error": "generate failed"})
    )
    driver = GrsaiVideoDriver(transport=httpx.MockTransport(recorder))

    with pytest.raises(BadRequest, match="提交失败") as excinfo:
        await driver.submit(_video_request())
    assert excinfo.value.http_status < 500


async def test_submit_upstream_5xx_stays_server_side() -> None:
    """上游 5xx 必须留在 5xx 那一侧。

    上游 5xx 意味着请求**已经发出**（可能已计费）→ 框架按「可能已受理」
    处理：不自动重试、不清提交意图标记。若把它降成 4xx，框架会当成
    「确定没送达」而自动重试 —— 同一张图可能被计费两次。
    """
    recorder = _Recorder(_json({"error": "internal error"}, status=500))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    with pytest.raises(UpstreamError) as excinfo:
        await driver.submit(_image_request())
    assert excinfo.value.http_status >= 500


# ------------------------------------------------------------------ 状态映射
async def test_poll_running() -> None:
    recorder = _Recorder(_json({"id": "task-1", "status": "running", "progress": 40}))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    progress = await driver.poll("task-1")

    assert progress.state is GenerationState.RUNNING
    assert progress.progress == 40
    assert progress.external_id == "task-1"
    assert recorder.url == "https://grsai.example/v1/api/result?id=task-1"


async def test_poll_succeeded_returns_url() -> None:
    recorder = _Recorder(
        _json(
            {
                "id": "task-1",
                "status": "succeeded",
                "results": [{"url": "https://file.example.com/out.png"}],
            }
        )
    )
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    progress = await driver.poll("task-1")

    assert progress.state is GenerationState.SUCCEEDED
    assert progress.output_url == "https://file.example.com/out.png"
    assert progress.cover_url == "https://file.example.com/out.png"


async def test_poll_failed_is_terminal_and_not_retryable() -> None:
    """失败是终态：重试只会重复计费。"""
    recorder = _Recorder(
        _json({"id": "task-1", "status": "failed", "error": "generate failed"})
    )
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    progress = await driver.poll("task-1")

    assert progress.state is GenerationState.FAILED
    assert progress.retryable is False
    assert "generate failed" in (progress.error or "")


async def test_poll_violation_is_terminal() -> None:
    recorder = _Recorder(_json({"id": "task-1", "status": "violation"}))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    progress = await driver.poll("task-1")

    assert progress.state is GenerationState.FAILED
    assert progress.retryable is False
    assert "违规" in (progress.error or "")


async def test_poll_unknown_task_is_terminal_not_retryable() -> None:
    """查不到任务（过期或 id 无效）是终态，不能一直重试到墙钟超时。"""
    recorder = _Recorder(_json({"error": "not found"}, status=404))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    progress = await driver.poll("task-gone")

    assert progress.state is GenerationState.FAILED
    assert progress.retryable is False
    assert "2 小时" in (progress.error or "")


async def test_poll_succeeded_without_url_fails_loudly() -> None:
    """说完成了却没给地址 —— 不能当成成功（否则前端拿到空产物）。"""
    recorder = _Recorder(_json({"id": "task-1", "status": "succeeded", "results": []}))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    progress = await driver.poll("task-1")

    assert progress.state is GenerationState.FAILED
    assert progress.retryable is False


# ------------------------------------------------------------------ 视频
def test_video_schema_declares_duration_and_aspect_ratio() -> None:
    """时长/画幅是顶层字段，但前端控件与入口校验都从 schema 读规则 —— 必须声明。

    漏了这条声明时：工作台选 wan-3.0 没有时长/画幅控件（用户只能吃驱动的 5s/
    9:16 兜底），且 `resolve_duration` 退回 1~600 兜底，超范围要等 worker 提交
    才失败。与百炼同一口径：规则进 schema，值仍走顶层列、不进 params。
    """
    spec = VIDEO_MODELS[0]
    duration = spec.param_schema["durationSec"]
    assert duration["type"] == "int"
    assert (duration["min"], duration["max"], duration["step"]) == (3, 30, 1)
    assert duration["default"] == 5

    aspect = spec.param_schema["aspectRatio"]
    assert aspect["type"] == "enum"
    assert set(aspect["options"]) == {"9:16", "16:9", "1:1"}
    assert aspect["default"] == "9:16"


async def test_video_submit_maps_duration_and_splits_media() -> None:
    """平台的 `durationSec` 要改名成上游的 `duration`；图与音频分数组。"""
    recorder = _Recorder(_json({"id": "task-v1", "status": "running"}))
    driver = GrsaiVideoDriver(transport=httpx.MockTransport(recorder))

    result = await driver.submit(
        _video_request(
            reference_urls=(
                "data:image/png;base64,AAAA",
                "data:audio/mpeg;base64,BBBB",
            )
        )
    )

    assert result == "task-v1"
    assert recorder.body["model"] == "wan-3.0"
    assert recorder.body["duration"] == 10
    assert recorder.body["resolution"] == "1080p"
    assert recorder.body["aspectRatio"] == "9:16"
    assert recorder.body["replyType"] == "async"
    assert recorder.body["images"] == ["data:image/png;base64,AAAA"]
    assert recorder.body["audios"] == ["data:audio/mpeg;base64,BBBB"]


async def test_video_submit_rejects_duration_out_of_range() -> None:
    """上游只收 3~30 秒，超出要在提交前拦下，别让任务白跑一趟。"""
    recorder = _Recorder(_json({"id": "task-v2", "status": "running"}))
    driver = GrsaiVideoDriver(transport=httpx.MockTransport(recorder))

    with pytest.raises(BadRequest, match="3~30 秒"):
        await driver.submit(_video_request(params={"durationSec": 60}))
    assert recorder.requests == []


def test_video_duration_2_is_rejected_at_entry() -> None:
    """2 秒看着在文档范围内，但上游实测拒绝（>2 秒）——入口就要挡住。

    2026-11-25 实测：提交 durationSec=2 被上游回
    "duration must be greater than 2 seconds"，任务当场 failed。
    文档写 2~30 是错的，schema 下界按实测改成 3，这里钉住入口校验用新下界，
    别让「文档说合法」的值走到供应商才失败。
    """
    from modules.task.params import resolve_duration

    with pytest.raises(BadRequest) as caught:
        resolve_duration(VIDEO_MODELS[0], 2)
    # 判据钉在「入口按新下界拒绝」，不是靠文案 —— 文案变了测试也该稳。
    assert caught.value.details == {"field": "durationSec", "min": 3, "max": 30}
    assert resolve_duration(VIDEO_MODELS[0], 3) == 3


async def test_video_submit_rejects_unsupported_aspect_ratio() -> None:
    """上游只收 16:9 / 9:16 / 21:9 / 4:3 / 1:1 / 3:4 —— 别的比例提前挡下。"""
    recorder = _Recorder(_json({"id": "task-v3", "status": "running"}))
    driver = GrsaiVideoDriver(transport=httpx.MockTransport(recorder))

    with pytest.raises(BadRequest, match="不支持画幅"):
        await driver.submit(_video_request(params={"aspectRatio": "4:5"}))
    assert recorder.requests == []


async def test_video_submit_rejects_too_many_references() -> None:
    recorder = _Recorder(_json({"id": "task-v4", "status": "running"}))
    driver = GrsaiVideoDriver(transport=httpx.MockTransport(recorder))

    with pytest.raises(BadRequest, match="最多 10 张参考图"):
        await driver.submit(
            _video_request(
                reference_urls=tuple(
                    f"data:image/png;base64,AAAA{i}" for i in range(11)
                )
            )
        )
    assert recorder.requests == []


async def test_video_poll_succeeded() -> None:
    recorder = _Recorder(
        _json(
            {
                "id": "task-v1",
                "status": "succeeded",
                "results": [{"url": "https://file.example.com/out.mp4"}],
            }
        )
    )
    driver = GrsaiVideoDriver(transport=httpx.MockTransport(recorder))

    progress = await driver.poll("task-v1")

    assert progress.state is GenerationState.SUCCEEDED
    assert progress.output_url == "https://file.example.com/out.mp4"


# ------------------------------------------------------------------ 标准符合性
def test_driver_passes_contract_validation() -> None:
    """驱动契约自检（返回值是问题清单，空 = 通过）。"""
    from modules.model_driver.spec import validate_driver

    assert validate_driver(GrsaiImageDriver) == []
    assert validate_driver(GrsaiVideoDriver) == []


def test_drivers_are_auto_loaded_and_routable() -> None:
    """两个驱动都靠目录扫描发现（不在任何白名单里），且模型 id 能路由。"""
    from infra import get_model_router
    from modules.model_driver.registry import (
        driver_summaries,
        ensure_drivers_registered,
        load_errors,
    )

    ensure_drivers_registered()
    assert not load_errors(), load_errors()

    infos = {item.name: item for item in driver_summaries()}
    assert infos["grsai"].loaded is True
    assert infos["grsai"].model_count == 6
    assert infos["grsai_video"].loaded is True
    assert infos["grsai_video"].model_count == 1

    router = get_model_router()
    assert router.resolve("grsai-nano-banana-2") is not None
    assert router.provider_for("grsai-nano-banana-2").name == "grsai"
    assert router.provider_for("grsai_video-wan-3.0").name == "grsai_video"


async def test_missing_api_key_fails_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没配密钥就别发请求（发了也只是拿 401）。"""
    monkeypatch.delenv("GRSAI_API_KEY", raising=False)
    recorder = _Recorder(_json({"id": "task-x", "status": "running"}))
    driver = GrsaiImageDriver(transport=httpx.MockTransport(recorder))

    with pytest.raises(BadRequest, match="GRSAI_API_KEY") as excinfo:
        await driver.submit(_image_request())
    # 必须是 4xx：框架按 HTTP 语义判「确定没送达 / 可能已受理」——
    # 4xx 会清掉提交意图标记、允许安全重试，并把真实原因给用户；
    # 5xx 则被改写成「提交结果未知，请核对上游任务」，而上游根本没有任务。
    assert excinfo.value.http_status < 500
    assert recorder.requests == []
